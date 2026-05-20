---
name: opik-integrations
version: 1.0.0
description: >
  Comprehensive catalog of Opik framework and provider integrations.
  Opik integration, OpenAI, Anthropic, Bedrock, Gemini, Cohere, DeepSeek, Groq, Ollama, LangChain, LangGraph, LlamaIndex, DSPy, Haystack, CrewAI, AutoGen, Pydantic AI, Smolagents, OpenAI Agents, Strands, Mastra, Vercel AI SDK, OpenTelemetry, LiteLLM, Microsoft Agent Framework, Semantic Kernel, Claude Agent SDK, Temporal.
---

## When to Use This Skill

Activate this skill when:

- Integrating Opik with LLM model providers (OpenAI, Anthropic, Bedrock, Cohere, Gemini, etc.)
- Building observability into agent frameworks (LangChain, LangGraph, CrewAI, AutoGen, DSPy, etc.)
- Setting up distributed tracing with OpenTelemetry or observability standards
- Logging and evaluating RAG pipelines with frameworks like LlamaIndex, Haystack
- Tracking LLM calls in TypeScript/JavaScript ecosystems (Vercel AI SDK, LangChain.js, etc.)
- Looking up integration documentation, configuration, or quickstart code
- Finding cost tracking and model support for a specific provider

---

## Coverage at a Glance

Opik provides ~80 integrations across five major categories:

### LLM Providers & SDKs

**OpenAI Ecosystem:** OpenAI (Python & TypeScript), Azure OpenAI, OpenRouter, OpenAI Agents  
**Claude/Anthropic:** Anthropic, Claude Agent SDK, Pydantic AI  
**AWS:** Bedrock (multi-model)  
**Open-Source & Alternatives:** Ollama, Groq, DeepSeek, Mistral, Together AI, Cohere, Fireworks, Novita, Predibase, X.AI  
**Specialized:** Byteplus, IBM Watsonx AI, Jina DeepSearch, Sambanova

### Agent Frameworks & Orchestration

**LangChain Family:** LangChain (Python), LangChain.js, LangGraph, LangServe  
**Full-Featured Agents:** CrewAI, AutoGen (AG2), Pydantic AI, Temporal, Haystack, DSPy  
**Next-Gen Platforms:** Strands Agents, Mastra, Agno, AgentSpec, BeeAI, Harbor, Voltagent, Pipecat, OpenAI Agents, Microsoft Agent Framework  
**Framework Tools:** Instructor, Semantic Kernel, Smolagents, ADK, LiveKit

### Observability & Standards

OpenTelemetry (distributed tracing), LiteLLM (gateway), RAGAS (RAG evaluation), Guardrails AI, Prediction Guard

### TypeScript/JavaScript Ecosystem

Vercel AI SDK, LangChain.js, Gemini TypeScript, BeeAI TypeScript, Mastra, OpenAI TypeScript SDK

### Java & .NET

.NET Framework, Java-compatible OpenTelemetry

---

## How Integrations Work

Opik integrations use **callbacks, decorators, or exporters** to intercept LLM calls and framework spans. For native integrations (OpenAI, LangChain), wrap your client or chain with Opik's tracker; for standards-based (OpenTelemetry), configure an OTLP exporter pointing to Opik's endpoint. Opik then logs inputs, outputs, token usage, latency, and costs—all searchable and evaluable in the Opik UI.

---

## Quickstart Snippet

### Python: OpenAI + Opik

```python
from opik.integrations.openai import track_openai
from openai import OpenAI
import os

os.environ["OPIK_PROJECT_NAME"] = "my-project"

client = OpenAI()
opik_client = track_openai(client)

completion = opik_client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(completion.choices[0].message.content)
```

### Python: LangChain + Opik

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from opik.integrations.langchain import OpikTracer

opik_tracer = OpikTracer(project_name="my-project")
llm = ChatOpenAI(model="gpt-4o")
prompt = ChatPromptTemplate.from_messages([("human", "{text}")])
chain = prompt | llm

result = chain.invoke(
    {"text": "Translate to French: Hello"},
    config={"callbacks": [opik_tracer]}
)
```

---

## Integration Index

### LLM Providers

- **OpenAI** → https://www.comet.com/docs/opik/integrations/openai
- **OpenAI TypeScript** → https://www.comet.com/docs/opik/integrations/openai-typescript
- **Anthropic** → https://www.comet.com/docs/opik/integrations/anthropic
- **Azure OpenAI** → https://www.comet.com/docs/opik/integrations/openai
- **Bedrock** → https://www.comet.com/docs/opik/integrations/bedrock
- **Gemini** → https://www.comet.com/docs/opik/integrations/gemini
- **Gemini TypeScript** → https://www.comet.com/docs/opik/integrations/gemini-typescript
- **Cohere** → https://www.comet.com/docs/opik/integrations/cohere
- **Groq** → https://www.comet.com/docs/opik/integrations/groq
- **Mistral** → https://www.comet.com/docs/opik/integrations/mistral
- **Ollama** → https://www.comet.com/docs/opik/integrations/ollama
- **DeepSeek** → https://www.comet.com/docs/opik/integrations/deepseek
- **Together AI** → https://www.comet.com/docs/opik/integrations/together-ai
- **OpenRouter** → https://www.comet.com/docs/opik/integrations/openrouter
- **Fireworks** → https://www.comet.com/docs/opik/integrations/fireworks
- **Novita** → https://www.comet.com/docs/opik/integrations/novita
- **X.AI (Grok)** → https://www.comet.com/docs/opik/integrations/xai
- **Predibase** → https://www.comet.com/docs/opik/integrations/predibase
- **Sambanova** → https://www.comet.com/docs/opik/integrations/sambanova
- **Byteplus** → https://www.comet.com/docs/opik/integrations/byteplus
- **IBM Watsonx AI** → https://www.comet.com/docs/opik/integrations/ibm-watsonx-ai
- **Jina DeepSearch** → https://www.comet.com/docs/opik/integrations/jina-deepsearch
- **Vercel AI SDK** → https://www.comet.com/docs/opik/integrations/vercel-ai-sdk

### Agent & Orchestration Frameworks

- **LangChain** → https://www.comet.com/docs/opik/integrations/langchain
- **LangChain.js** → https://www.comet.com/docs/opik/integrations/langchainjs
- **LangGraph** → https://www.comet.com/docs/opik/integrations/langgraph
- **LangServe** → https://www.comet.com/docs/opik/integrations/langserve
- **LlamaIndex** → https://www.comet.com/docs/opik/integrations/llama_index
- **CrewAI** → https://www.comet.com/docs/opik/integrations/crewai
- **AutoGen (AG2)** → https://www.comet.com/docs/opik/integrations/ag2
- **Pydantic AI** → https://www.comet.com/docs/opik/integrations/pydantic-ai
- **DSPy** → https://www.comet.com/docs/opik/integrations/dspy
- **Haystack** → https://www.comet.com/docs/opik/integrations/haystack
- **Instructor** → https://www.comet.com/docs/opik/integrations/instructor
- **Semantic Kernel** → https://www.comet.com/docs/opik/integrations/semantic-kernel
- **Microsoft Agent Framework** → https://www.comet.com/docs/opik/integrations/microsoft-agent-framework
- **OpenAI Agents** → https://www.comet.com/docs/opik/integrations/openai_agents
- **Claude Agent SDK** → https://www.comet.com/docs/opik/integrations/claude-agent-sdk
- **Strands Agents** → https://www.comet.com/docs/opik/integrations/strands-agents
- **Mastra** → https://www.comet.com/docs/opik/integrations/mastra
- **Temporal** → https://www.comet.com/docs/opik/integrations/temporal
- **Smolagents** → https://www.comet.com/docs/opik/integrations/smolagents
- **BeeAI** → https://www.comet.com/docs/opik/integrations/beeai
- **BeeAI TypeScript** → https://www.comet.com/docs/opik/integrations/beeai-typescript
- **Agno** → https://www.comet.com/docs/opik/integrations/agno
- **AgentSpec** → https://www.comet.com/docs/opik/integrations/agentspec
- **ADK** → https://www.comet.com/docs/opik/integrations/adk
- **LiveKit** → https://www.comet.com/docs/opik/integrations/livekit
- **Pipecat** → https://www.comet.com/docs/opik/integrations/pipecat
- **Harbor** → https://www.comet.com/docs/opik/integrations/harbor
- **Voltagent** → https://www.comet.com/docs/opik/integrations/voltagent

### Observability & Infrastructure

- **OpenTelemetry** → https://www.comet.com/docs/opik/integrations/opentelemetry
- **LiteLLM** → https://www.comet.com/docs/opik/integrations/litellm
- **RAGAS** → https://www.comet.com/docs/opik/integrations/ragas
- **Guardrails AI** → https://www.comet.com/docs/opik/integrations/guardrails-ai
- **Prediction Guard** → https://www.comet.com/docs/opik/integrations/predictionguard
- **Cloudflare Workers AI** → https://www.comet.com/docs/opik/integrations/cloudflare-workers-ai

### TypeScript SDK Family

- **TypeScript SDK** → https://www.comet.com/docs/opik/integrations/typescript-sdk
- **Vercel AI SDK** → https://www.comet.com/docs/opik/integrations/vercel-ai-sdk
- **OpenAI TypeScript** → https://www.comet.com/docs/opik/integrations/openai-typescript
- **LangChain.js** → https://www.comet.com/docs/opik/integrations/langchainjs
- **Gemini TypeScript** → https://www.comet.com/docs/opik/integrations/gemini-typescript
- **BeeAI TypeScript** → https://www.comet.com/docs/opik/integrations/beeai-typescript
- **Mastra** → https://www.comet.com/docs/opik/integrations/mastra

---

## Reference

**Documentation Home:** https://www.comet.com/docs/opik/integrations/overview

**Configuration & Setup:**
- Python SDK Configuration: https://www.comet.com/docs/opik/tracing/advanced/sdk_configuration
- TypeScript SDK Setup: https://www.comet.com/docs/opik/typescript-sdk-reference/
- Self-hosted Deployment: https://www.comet.com/docs/opik/self-host/overview

**Cost Tracking & Evaluation:**
- Supported Models & Pricing: https://www.comet.com/docs/opik/tracing/supported_models
- Evaluation & Testing: https://www.comet.com/docs/opik/evaluation/overview

**Community:**
- GitHub Issues: https://github.com/comet-ml/opik/issues
- Request New Integrations: https://github.com/comet-ml/opik/issues/new/choose
