# Cingulater: Autonomous Agent Engine

Cingulater is an autonomous agent execution engine equipped with a pipeline that monitors, intervenes in, and modifies the inference stream of LLMs (Large Language Models) in real-time.

It is not just a system that passes user prompts to an LLM and returns the results. The true value of Cingulater lies in its **Interceptor Architecture**, which absorbs the uncertainties of LLM outputs (such as hallucinations or lack of action) at the system level and automatically ensures task completion.

![Brownie Banner](docs/images/banner.jpeg)

## 🧠 Core Autonomous Mechanisms

The basis for Cingulater's autonomy lies in the fact that the system makes independent judgments and repairs through the following implementation logic.

### 1. Reflection & Action Completion

LLMs often suffer from the problem of "being satisfied with just answering in text and forgetting to execute the actual tool (function)." The `ReflectionInterceptor` solves this problem autonomously.

* **Detection**: Monitors the LLM's output stream and intervenes if the workflow finishes without a single `ToolCall` occurring.
* **Intent Extraction**: Extracts the user's true intent as a short English phrase from the end of the generated response.
* **Semantic Search**: Compares the extracted intent against the documentation of all available tools. During this process, it calls an external Reranker (`mcp-reranker`) via MCP (Model Context Protocol) to score and determine the most suitable tool.
* **Forced Execution**: Once the optimal tool is found, the system fires a `SystemToolCallEvent` on behalf of the LLM, mapping the response content to arguments and automatically executing the tool.

### 2. Self-Healing from Hallucinations

When an LLM makes up and tries to call a "non-existent tool," it usually results in a system error. The `ToolHallucinationInterceptor` prevents this.

* **Anomaly Detection**: Detects in real-time when a tool name emitted on the stream does not exist in the toolset currently registered in the system.
* **Parameter Reconstruction**: Buffers the argument stream intended for the non-existent tool and remaps the arguments to fit the schema of the safest alternative (fallback) tool.
* **Transparent Replacement**: Rewrites the stream as a call to a valid tool and continues execution without returning errors to the user or the LLM.

## ✨ Key Features

### 🔌 Full OpenAI API Compatibility

The FastAPI-based server is fully compatible with OpenAI's `/v1/chat/completions` endpoint. It strictly follows the formats for streaming (SSE) and function calling, allowing you to use Cingulater as a backend without any modifications to your existing OpenAI ecosystem (SDKs, GUI clients, agent frameworks).

### ⚙️ Flexible Model Configuration (`config.yaml`)

LLM models and connection destinations are not hardcoded; they can be easily changed just by editing `config.yaml`.

* **Change Models**: You can freely specify the model to use (e.g., `mlx-community/Qwen3.5-9B-MLX-4bit`) by editing the `llm.models` section.
* **Specify Endpoints**: You can configure the destination endpoint URL (`interlocutor_endpoint`), whether it's a local inference server or an external API.
* **Dynamic Launcher**: By configuring the `launcher_client` and `launcher_tool`, the Orchestrator can automatically launch the specified model via MCP (e.g., starting a local LLM using `mlx-launcher`) upon startup.

### 🛠️ MCP (Model Context Protocol) Integration

Dynamically connects and fetches local processes and external tools using a standardized protocol via `mcp_config.json`. This allows for a safe and loosely-coupled expansion of the agent's capabilities (toolset).

## 🏗️ Architecture

Cingulater is built as an asynchronous streaming server and consists of the following components:

* **Orchestrator**: Governs the entire process, managing the lifecycle of MCP clients and controlling requests to the LLM client.
* **Interceptor Pipeline**: Connects request pre-processing and response stream post-processing in series. It includes logging, context limits, system prompt injection, and the self-healing logic mentioned above.
* **Workflow Pipeline**: Manages the progression of tasks centered around the actor (LLM), such as tool fetching and action execution.

## 🚀 Getting Started

### 1. Installation & Setup

Automatically builds the environment, including necessary dependencies like `uv` and `git-lfs`, and downloads models based on `config.yaml`.

```bash
./bin/setup.sh
```

### 2. Start the Server

The Orchestrator is initialized, MCP client connections are established, and the API server starts on port `8137`.

```bash
./bin/cingulater start
```

### 3. Interactive CLI

After starting the server, you can interact directly with the engine from the CLI tool to observe the autonomous behavior of the agent.

```bash
./bin/cingulater chat
```

### 4. Cleanup

Safely removes the built virtual environment, caches, databases, etc.

```bash
./bin/unsetup.sh
```

## 📜 License

This project is licensed under the **GNU General Public License v3.0**. Please refer to the `LICENSE` file in the repository for details.