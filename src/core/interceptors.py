# src/core/interceptors.py
import json
import time
import yaml
from typing import AsyncGenerator, Protocol, List, Optional
from loguru import logger

from core.schema import InternalAgentRequest, InternalTool, InternalMessage
from core.events import (
    AgentEvent,
    TextDeltaEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    SystemToolCallEvent,
    WorkflowFinishEvent,
    ErrorEvent
)

# ==========================================
# LLM Layer Interceptors
# ==========================================
class Interceptor(Protocol):
    async def pre_process(self, request: InternalAgentRequest, orchestrator, **kwargs) -> InternalAgentRequest:
        ...

    async def post_process_stream(
        self, 
        stream: AsyncGenerator[AgentEvent, None], 
        request: InternalAgentRequest, 
        orchestrator,
        **kwargs
    ) -> AsyncGenerator[AgentEvent, None]:
        ...

class BaseInterceptor:
    async def pre_process(self, request: InternalAgentRequest, orchestrator, **kwargs) -> InternalAgentRequest:
        return request

    async def post_process_stream(
        self, 
        stream: AsyncGenerator[AgentEvent, None], 
        request: InternalAgentRequest, 
        orchestrator,
        **kwargs
    ) -> AsyncGenerator[AgentEvent, None]:
        async for event in stream:
            yield event

class LoggingInterceptor(BaseInterceptor):
    async def pre_process(self, request: InternalAgentRequest, orchestrator, **kwargs) -> InternalAgentRequest:
        model_key = kwargs.get("model_key", "unknown")
        logger.debug(f"[Interceptor] Preparing LLM Request for actor '{model_key}'. Messages: {len(request.messages)}")
        return request

    async def post_process_stream(self, stream, request, orchestrator, **kwargs):
        token_count = 0
        tool_calls = 0
        async for event in stream:
            if isinstance(event, TextDeltaEvent):
                token_count += 1
            elif isinstance(event, (ToolCallStartEvent, SystemToolCallEvent)):
                tool_calls += 1
            yield event
        logger.debug(f"[Interceptor] LLM Stream Finished. Approx Tokens: {token_count}, Tool Calls: {tool_calls}")

class ContextLimitInterceptor(BaseInterceptor):
    def __init__(self, max_messages: int = 20):
        self.max_messages = max_messages

    async def pre_process(self, request: InternalAgentRequest, orchestrator, **kwargs) -> InternalAgentRequest:
        if len(request.messages) > self.max_messages:
            sys_msg = request.messages[0] if request.messages and request.messages[0].role == "system" else None
            kept = request.messages[-(self.max_messages - 1):]
            if sys_msg and (not kept or kept[0] != sys_msg):
                request.messages = [sys_msg] + kept
            else:
                request.messages = kept
            logger.warning(f"[Interceptor] Context limit exceeded. Truncated to {len(request.messages)} messages.")
        return request

class SystemPromptInterceptor(BaseInterceptor):
    async def pre_process(self, request: InternalAgentRequest, orchestrator, **kwargs) -> InternalAgentRequest:
        system_prompt = orchestrator.system_prompt
        if request.messages and request.messages[0].role == "system":
            request.messages[0].content = system_prompt + "\n\n" + (request.messages[0].content or "")
        else:
            request.messages.insert(0, InternalMessage(role="system", content=system_prompt))
        return request

class ModelConfigurationInterceptor(BaseInterceptor):
    async def pre_process(self, request: InternalAgentRequest, orchestrator, **kwargs) -> InternalAgentRequest:
        model_key = kwargs.get("model_key", "default")
        request.model = orchestrator.settings.llm.models.get(model_key, "default")
        request.stream = True
        return request

class ToolHallucinationInterceptor(BaseInterceptor):
    async def post_process_stream(
        self, 
        stream: AsyncGenerator[AgentEvent, None], 
        request: InternalAgentRequest, 
        orchestrator,
        **kwargs
    ) -> AsyncGenerator[AgentEvent, None]:
        
        available_tools_dict = {
            t.function.get("name"): t.function
            for t in (request.tools or []) 
            if t.function and t.function.get("name")
        }
        available_tool_names = list(available_tools_dict.keys())
        hallucinated_indexes = {}

        async for event in stream:
            if isinstance(event, ToolCallStartEvent):
                func_name = event.tool_name
                if func_name and func_name not in available_tool_names and available_tool_names:
                    fallback_name = available_tool_names[0]
                    logger.warning(f"[BROWNIE DEBUG] Tool '{func_name}' is NOT available! Rewriting to '{fallback_name}'.")
                    hallucinated_indexes[event.index] = {
                        "id": event.id,
                        "fallback_name": fallback_name,
                        "args_buffer": ""
                    }
                    continue
                yield event
            
            elif isinstance(event, ToolCallDeltaEvent):
                if event.index in hallucinated_indexes:
                    hallucinated_indexes[event.index]["args_buffer"] += event.arguments
                    continue
                yield event
            
            elif isinstance(event, WorkflowFinishEvent):
                for idx, data in hallucinated_indexes.items():
                    args_str = data["args_buffer"]
                    fallback_name = data["fallback_name"]
                    tc_id = data["id"]
                    
                    try:
                        args = json.loads(args_str) if args_str else {}
                    except Exception:
                        args = {"raw_args": args_str}

                    text_val = ""
                    for v in args.values():
                        if isinstance(v, str) and len(v) > len(text_val):
                            text_val = v
                    if not text_val: text_val = str(args)
                    
                    tool_schema = available_tools_dict[fallback_name]
                    fallback_props = tool_schema.get("parameters", {}).get("properties", {})
                    fallback_required = tool_schema.get("parameters", {}).get("required", [])
                    
                    fallback_param_name = "text"
                    for pref in ["result", "question", "message", "content", "response"]:
                        if pref in fallback_props:
                            fallback_param_name = pref
                            break
                    else:
                        if fallback_props: fallback_param_name = list(fallback_props.keys())[0]

                    new_args = {fallback_param_name: text_val}
                    for req in fallback_required:
                        if req != fallback_param_name:
                            ptype = fallback_props.get(req, {}).get("type", "string")
                            if ptype == "array": new_args[req] = []
                            elif ptype == "object": new_args[req] = {}
                            elif ptype == "boolean": new_args[req] = False
                            elif ptype in ["number", "integer"]: new_args[req] = 0
                            else: new_args[req] = ""
                    
                    yield SystemToolCallEvent(index=idx, id=tc_id, tool_name=fallback_name, arguments=new_args)
                yield event
            else:
                yield event

class ReflectionInterceptor(BaseInterceptor):
    async def post_process_stream(
        self, 
        stream: AsyncGenerator[AgentEvent, None], 
        request: InternalAgentRequest, 
        orchestrator,
        **kwargs
    ) -> AsyncGenerator[AgentEvent, None]:
        
        full_content = ""
        has_tool_calls = False

        async for event in stream:
            if isinstance(event, TextDeltaEvent):
                full_content += event.content
                yield event
            elif isinstance(event, (ToolCallStartEvent, SystemToolCallEvent)):
                has_tool_calls = True
                yield event
            elif isinstance(event, WorkflowFinishEvent):
                if not has_tool_calls and request.tools and full_content:
                    reflection_event = await self._evaluate(full_content, request.tools, orchestrator)
                    if reflection_event:
                        yield reflection_event
                        yield WorkflowFinishEvent(finish_reason="tool_calls")
                        continue
                yield event
            else:
                yield event

    async def _evaluate(self, full_content: str, available_tools: List[InternalTool], orchestrator) -> Optional[SystemToolCallEvent]:
        available_tools_dict = {
            t.function.get("name"): t.function
            for t in available_tools
            if t.function and t.function.get("name")
        }
        if not available_tools_dict:
            return None

        available_tool_names = list(available_tools_dict.keys())

        logger.info("[BROWNIE DEBUG] --- Reflection Phase Started (Using Reranker) ---")
        
        intent = await orchestrator._extract_intent(full_content[-1000:])
        docs = []
        for tn in available_tool_names:
            desc = available_tools_dict[tn].get("description", "No description provided")
            docs.append(desc)
        
        selected_tool = available_tool_names[0]
        try:
            reranker_client = orchestrator.mcp_clients.get("mcp-reranker")
            if reranker_client:
                result_str = await reranker_client.call_tool(
                    "rerank_documents", 
                    {"query": intent, "documents": docs}
                )
                results = json.loads(result_str)
                if results:
                    best_doc = results[0]["document"]
                    best_idx = docs.index(best_doc)
                    selected_tool = available_tool_names[best_idx]
                    logger.info(f"[BROWNIE DEBUG] Reflection selected tool '{selected_tool}' with score {results[0]['score']:.4f} (Intent: {intent})")
            else:
                logger.warning("[BROWNIE DEBUG] mcp-reranker client not connected. Using default first tool.")
        except Exception as e:
            logger.error(f"[BROWNIE DEBUG] Reflection Reranker Error: {e}")
            
        tool_schema = available_tools_dict[selected_tool]
        props = tool_schema.get("parameters", {}).get("properties", {})
        reqs = tool_schema.get("parameters", {}).get("required", [])
        
        args = {}
        for req in reqs:
            req_lower = req.lower()
            if any(k in req_lower for k in ["question", "ask", "result", "summary", "message", "text", "content", "response"]):
                args[req] = full_content.strip()
            else:
                ptype = props.get(req, {}).get("type", "string")
                if ptype == "array": args[req] = []
                elif ptype == "object": args[req] = {}
                elif ptype == "boolean": args[req] = False
                elif ptype in ["number", "integer"]: args[req] = 0
                else: args[req] = full_content.strip()
        
        tc_id = f"call_ref_{int(time.time())}"
        return SystemToolCallEvent(
            index=0, 
            id=tc_id, 
            tool_name=selected_tool,
            arguments=args
        )

class ErrorHandlingInterceptor(BaseInterceptor):
    async def post_process_stream(
        self, 
        stream: AsyncGenerator[AgentEvent, None], 
        request: InternalAgentRequest, 
        orchestrator,
        **kwargs
    ) -> AsyncGenerator[AgentEvent, None]:
        try:
            async for event in stream:
                yield event
        except Exception as e:
            logger.error(f"[Interceptor] LLM Streaming Exception caught: {e}")
            yield ErrorEvent(message=f"Connection Error: {e}")

class InterceptorPipeline:
    def __init__(self, interceptors: List[Interceptor]):
        self.interceptors = interceptors

    async def pre_process(self, request: InternalAgentRequest, orchestrator, **kwargs) -> InternalAgentRequest:
        req = request
        for interceptor in self.interceptors:
            req = await interceptor.pre_process(req, orchestrator, **kwargs)
        return req

    async def post_process_stream(
        self, 
        stream: AsyncGenerator[AgentEvent, None], 
        request: InternalAgentRequest, 
        orchestrator,
        **kwargs
    ) -> AsyncGenerator[AgentEvent, None]:
        current_stream = stream
        for interceptor in self.interceptors:
            current_stream = interceptor.post_process_stream(current_stream, request, orchestrator, **kwargs)
        
        async for event in current_stream:
            yield event


# ==========================================
# Workflow Layer Interceptors
# ==========================================
class WorkflowInterceptor(Protocol):
    async def pre_process(self, actor: str, request: InternalAgentRequest, orchestrator, **kwargs) -> dict:
        ...

    def post_process_stream(
        self,
        stream: AsyncGenerator[AgentEvent, None],
        actor: str,
        request: InternalAgentRequest,
        orchestrator,
        **kwargs
    ) -> AsyncGenerator[AgentEvent, None]:
        ...

class BaseWorkflowInterceptor:
    async def pre_process(self, actor: str, request: InternalAgentRequest, orchestrator, **kwargs) -> dict:
        return kwargs

    async def post_process_stream(self, stream, actor, request, orchestrator, **kwargs):
        async for event in stream:
            yield event

class WorkflowLoadInterceptor(BaseWorkflowInterceptor):
    async def pre_process(self, actor: str, request: InternalAgentRequest, orchestrator, **kwargs) -> dict:
        workflow_path = orchestrator.workflows_dir / f"{actor}.yaml"
        if not workflow_path.exists():
            raise FileNotFoundError("Workflow not found")
        
        try:
            with open(workflow_path, "r", encoding="utf-8") as f:
                kwargs["workflow_steps"] = yaml.safe_load(f).get("steps", [])
        except Exception as e:
            raise ValueError(f"Workflow parse error: {e}")
        
        return kwargs

class ToolFetchInterceptor(BaseWorkflowInterceptor):
    async def pre_process(self, actor: str, request: InternalAgentRequest, orchestrator, **kwargs) -> dict:
        fetched_tools = []
        for name, client in orchestrator.mcp_clients.items():
            try:
                tools = await client.fetch_tools()
                for t in tools:
                    fetched_tools.append((client, t))
            except Exception as e:
                logger.warning(f"Failed to fetch tools from {name}: {e}")
        kwargs["fetched_mcp_tools"] = fetched_tools
        return kwargs

class WorkflowExecutionInterceptor(BaseWorkflowInterceptor):
    async def post_process_stream(self, stream, actor, request, orchestrator, **kwargs):
        try:
            async for event in stream:
                yield event
        except Exception as e:
            logger.error(f"[Workflow Execution Error] {e}")
            yield ErrorEvent(message=f"Workflow execution failed: {e}")

class WorkflowInterceptorPipeline:
    def __init__(self, interceptors: List[WorkflowInterceptor]):
        self.interceptors = interceptors

    async def process(self, actor: str, request: InternalAgentRequest, orchestrator, core_func) -> AsyncGenerator[AgentEvent, None]:
        kwargs = {}
        try:
            for interceptor in self.interceptors:
                kwargs = await interceptor.pre_process(actor, request, orchestrator, **kwargs)
        except Exception as e:
            yield ErrorEvent(message=str(e))
            return

        stream = core_func(actor, request, **kwargs)

        for interceptor in reversed(self.interceptors):
            stream = interceptor.post_process_stream(stream, actor, request, orchestrator, **kwargs)

        async for event in stream:
            yield event