# src/core/orchestrator.py
import os
import time
import yaml
import json
import asyncio
import logging
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncGenerator
from contextlib import AsyncExitStack

import httpx
from loguru import logger
from smolagents import Tool, ToolCallingAgent, OpenAIServerModel
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

import mcp.types as types

from core.schema import InternalAgentRequest, InternalMessage, InternalTool
from core.events import (
    AgentEvent,
    TextDeltaEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    SystemToolCallEvent,
    WorkflowFinishEvent,
    ErrorEvent
)
from core.llm_client import OpenAILLMClient
from core.interceptors import (
    InterceptorPipeline,
    SystemPromptInterceptor,
    ToolHallucinationInterceptor,
    ModelConfigurationInterceptor,
    ErrorHandlingInterceptor,
    LoggingInterceptor,
    ContextLimitInterceptor,
    WorkflowInterceptorPipeline,
    WorkflowLoadInterceptor,
    WorkflowExecutionInterceptor
)


# ==========================================
# 1. Config & Settings
# ==========================================
class AgentSettings(BaseModel):
    max_retries: int = Field(default=3)

class LLMSettings(BaseModel):
    models: dict[str, str] = Field(default_factory=dict)
    interlocutor_endpoint: str = Field(default="http://localhost:8080/v1")
    coder_endpoint: str = Field(default="http://localhost:8081/v1")
    timeout_sec: int = Field(default=120)
    launcher_client: Optional[str] = Field(default="mlx-launcher")
    launcher_tool: Optional[str] = Field(default="launch_llm_server")

class WorkspaceSettings(BaseModel):
    sandbox_user: str = Field(default="cingulater_sandbox")
    base_path: str = Field(default="./workspace")

class Settings(BaseSettings):
    agent: AgentSettings = Field(default_factory=AgentSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)

    @classmethod
    def load(cls, config_path: str) -> "Settings":
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}
            return cls(**yaml_data)
        except Exception as e:
            logger.error(f"Failed to load config from {config_path}: {e}")
            return cls()

def get_settings(config_path: str = "config.yaml") -> Settings:
    return Settings.load(config_path)


# ==========================================
# 2. Core Orchestrator
# ==========================================
class Orchestrator:
    def __init__(self, config_path: str):
        self.settings = get_settings(config_path)
        self.project_root = Path(__file__).parent.parent.parent
        self.workflows_dir = self.project_root / "workflows"
        self.system_prompt_path = self.project_root / ".cingulater" / "system_prompt.md"
        
        self.system_prompt = self._load_system_prompt()
        self.http_client = httpx.AsyncClient(timeout=self.settings.llm.timeout_sec)
        self.llm_client = OpenAILLMClient()
        
        self.llm_pipeline = InterceptorPipeline([
            LoggingInterceptor(),
            ContextLimitInterceptor(max_messages=20),
            SystemPromptInterceptor(),
            ModelConfigurationInterceptor(),
            ToolHallucinationInterceptor(),
            ErrorHandlingInterceptor()
        ])

        self.workflow_pipeline = WorkflowInterceptorPipeline([
            WorkflowLoadInterceptor(),
            WorkflowExecutionInterceptor()
        ])

    async def start(self):
        logger.info("✅ Orchestrator: Hybrid-Workflow engine ready.")

    def _load_system_prompt(self) -> str:
        if self.system_prompt_path.exists():
            return self.system_prompt_path.read_text(encoding="utf-8")
        return "You are CINGULATER."

    async def process_workflow(self, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        actor = "interlocutor"
        workflow_steps = [{"type": "llm_chat", "model_key": "interlocutor"}]
        logger.info(f"Selected Actor: {actor}")
        
        async for event in self.workflow_pipeline.process(actor, request, self, self._raw_run_workflow, workflow_steps=workflow_steps):
            yield event

    async def _raw_run_workflow(self, actor: str, request: InternalAgentRequest, **kwargs) -> AsyncGenerator[AgentEvent, None]:
        steps = kwargs.get("workflow_steps", [])
        final_reason = "stop"

        for i, step in enumerate(steps):
            model_key = step.get("model_key")
            if not model_key:
                logger.error(f"Validation Error: Step {i+1} is missing required 'model_key'.")
                yield ErrorEvent(message=f"Step {i+1} is missing required 'model_key'.")
                return

            endpoint = getattr(self.settings.llm, f"{model_key}_endpoint", self.settings.llm.interlocutor_endpoint)
            
            async for event in self._call_llm(model_key, endpoint, request):
                if isinstance(event, WorkflowFinishEvent):
                    final_reason = event.finish_reason
                else:
                    yield event
        
        yield WorkflowFinishEvent(finish_reason=final_reason)

    async def _call_llm(self, model_key: str, endpoint: str, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        processed_request = await self.llm_pipeline.pre_process(
            request.model_copy(deep=True), self, model_key=model_key, endpoint=endpoint
        )
        raw_stream = self._raw_stream_llm(model_key, endpoint, processed_request)
        async for event in self.llm_pipeline.post_process_stream(
            raw_stream, processed_request, self, model_key=model_key, endpoint=endpoint
        ):
            yield event

    async def _raw_stream_llm(self, model_key: str, endpoint: str, request: InternalAgentRequest) -> AsyncGenerator[AgentEvent, None]:
        json_payload = request.model_dump(exclude_none=True)
        final_finish_reason = "stop"
        
        async for chunk in self.llm_client.stream_chat(endpoint, json_payload, self.settings.llm.timeout_sec):
            if chunk.content:
                yield TextDeltaEvent(content=chunk.content)
            
            if chunk.tool_calls:
                for tc in chunk.tool_calls:
                    func_name = tc.name
                    args_str = tc.arguments or ""
                    tc_id = tc.id or f"call_{tc.index}"
                    
                    if func_name:
                        yield ToolCallStartEvent(index=tc.index, id=tc_id, tool_name=func_name)
                    if args_str:
                        yield ToolCallDeltaEvent(index=tc.index, arguments=args_str)
                        
            if chunk.finish_reason:
                final_finish_reason = chunk.finish_reason

        yield WorkflowFinishEvent(finish_reason=final_finish_reason)

    async def shutdown(self):
        await self.http_client.aclose()