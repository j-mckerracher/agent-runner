---
name: opik-prompt-engineering
version: 1.0.0
description: >
  Opik v1 prompt management, versioning, and experimentation for LLMs.
  Use this skill when building prompt libraries, managing prompt versions, using the Prompt Playground,
  improving prompts with AI, or integrating Opik via the prompt MCP server in IDEs.
  Keywords: Opik, prompt management, prompt versioning, Prompt Playground, prompt library,
  Prompt class, text prompts, chat prompts, LLM proxy, prompt MCP server, improve prompt,
  Cursor, VS Code, Windsurf.
---

## When to Use This Skill

Activate this skill when:

- Creating or managing prompts in the Opik prompt library (text or chat formats)
- Versioning prompts using the `Prompt` class in Python or TypeScript
- Experimenting with different prompts in the Prompt Playground UI
- Running prompt experiments against datasets
- Using the AI-powered Prompt Generator or Prompt Improver
- Setting up the Opik MCP server in Cursor, VS Code, or Windsurf
- Fetching and using prompts by name and version in code

---

## Key Concepts

**Prompt Library:** Centralized repository for versioning and managing prompts across projects. Supports both code-based and UI-based management.

**Prompt Types:**
- **Text Prompts:** String-based templates with `{{variable}}` substitution for single-turn interactions
- **Chat Prompts:** OpenAI-format structured messages for multi-turn conversational AI with multimodal support (text, images, videos)

**Prompt Versioning:** Automatic version tracking when using the `Prompt` class or library UI. Safely run the same code multiple times without creating duplicates.

**Prompt Playground:** Interactive UI for testing prompts against LLMs (OpenAI, Anthropic, Gemini, etc.) and datasets. Supports variables (`{{var}}`), images, and model parameter tuning.

**Prompt Generator/Improver:** AI-powered tools that refactor prompts using industry best practices from OpenAI, Google, and Anthropic.

**MCP Server:** Model Context Protocol integration allowing IDE-native access to Opik prompts and traces (Cursor, VS Code, Windsurf).

---

## Common Tasks

| Task | Documentation |
|------|---|
| Create and version text or chat prompts | https://www.comet.com/docs/opik/v1/prompt_engineering/prompt_management |
| Use Prompt Playground for interactive testing | https://www.comet.com/docs/opik/v1/prompt_engineering/playground |
| Run experiments with datasets | https://www.comet.com/docs/opik/v1/prompt_engineering/playground |
| Generate or improve prompts with AI | https://www.comet.com/docs/opik/v1/prompt_engineering/improve |
| Integrate Opik with IDE via MCP server | https://www.comet.com/docs/opik/v1/prompt_engineering/mcp_server |

---

## Development Best Practices

1. **Version prompts and configuration changes by default.** Treat prompts like code: use named prompts, explicit metadata, and version history so you can compare and roll back safely.
2. **Test changes before deployment.** Use Prompt Playground or Agent Playground workflows to try unsaved prompt/config changes before making them active.
3. **Prefer configuration changes over code redeploys when possible.** Opik's development model is built around updating prompts, models, and parameters through managed configuration, then letting the runtime pull the active version.
4. **Pair prompt edits with evaluations.** After changing a prompt, run dataset-backed comparisons or test suites instead of relying on a few spot checks.
5. **Keep dataset variables aligned with prompt variables.** If a prompt expects `{{input}}`, `{{context}}`, or nested JSON fields, make sure the evaluation dataset uses matching column names.
6. **Make one change at a time when iterating.** When comparing prompts, model settings, or system instructions, isolate the variable you changed so experiment results stay interpretable.
7. **Use tracing while developing prompts.** Prompt iteration is faster when each playground or agent run is traceable down to retrieval, tool use, and final model output.
8. **Promote successful experiments into stable configuration.** Once a prompt works in playground testing and evaluation runs, deploy that version intentionally instead of keeping ad hoc local copies.

**Development references**
- Development overview: https://www.comet.com/docs/opik/development/overview
- Prompt Playground: https://www.comet.com/docs/opik/development/prompt-playground
- Agent configuration: https://www.comet.com/docs/opik/development/agent-configuration/overview
- Optimization runs: https://www.comet.com/docs/opik/development/optimization-runs/overview

---

## Code Snippets

### Create and Use a Text Prompt (Python)

```python
import opik

# Create a text prompt with variables
prompt = opik.Prompt(
    name="summarize-text",
    prompt="Write a summary of the following text: {{text}}",
    metadata={"environment": "production"},
    project_name="my-project"
)

# Format and use
summary = prompt.format(text="Long document content...")
print(summary)
```

### Create and Use a Chat Prompt (TypeScript)

```typescript
import { Prompt } from "opik";

const prompt = new Prompt({
  name: "customer-support",
  prompt: [
    { role: "system", content: "You are a helpful customer support agent." },
    { role: "user", content: "Help with: {{customer_issue}}" }
  ],
  projectName: "support-team"
});

// Fetch by name and version
const latestPrompt = await Prompt.fetch("customer-support");
console.log(latestPrompt.prompt);
```

---

## Reference

- **Prompt Class (Python):** Automatically creates new prompts or fetches existing ones by name
- **Prompt Class (TypeScript):** Manages prompts with camelCase config (e.g., `projectName`, `metadata`)
- **Low-level SDK:** For granular control over prompt creation, updates, and deletion via client methods
- **Playground Support:** OpenAI, Anthropic, OpenRouter, Gemini, Vertex AI, Azure OpenAI, Amazon Bedrock
- **Dataset Integration:** Align prompt variables (`{{column_name}}`) with dataset columns to run batch experiments
- **Image Support:** Use `{{image_column}}` or add images via UI in vision-capable model tests
- **Nested JSON:** Dot notation for dataset fields (e.g., `{{input.user.name}}`)

**MCP Server Setup:**
- Cursor: `npx -y opik-mcp --apiKey YOUR_API_KEY`
- VS Code: Add to `.vscode/mcp.json`
- Windsurf: Add to `mcp_config.json`
- Test: Ask "What is the latest trace available in Opik?"
