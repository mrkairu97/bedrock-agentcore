---
inclusion: always
---

# Strands Agents SDK & AgentCore CLI

This project (Returns & Refunds Assistant) is built with the **Strands Agents SDK** and deployed/managed with the **AgentCore CLI**. These rules govern how to work with both.

## Look up documentation before writing code

- Before writing or editing any Python code that touches **AgentCore** (CLI config, `agentcore.json`, runtime, gateway, memory, identity, harness) or the **Strands Agents SDK** (agents, tools, models, session managers, multi-agent patterns), first search the relevant documentation using the MCP tools:
  - `strands-agents` MCP server tools: `search_docs`, `fetch_doc` — for Strands SDK concepts, APIs, and patterns (strandsagents.com).
  - `awslabs.aws-documentation-mcp-server` MCP tools: `search_documentation`, `read_documentation`, etc. — for AgentCore service documentation (docs.aws.amazon.com).
- Do not rely on prior/trained knowledge of Strands or AgentCore APIs — both evolve quickly. Always confirm current syntax, parameter names, and recommended patterns via MCP before writing the code, and cite the doc URL(s) consulted.
- If the user supplies a specific doc URL in their request, fetch that URL (via `fetch_doc` or `read_documentation` as appropriate) rather than relying on search alone.

## Tool pattern

- All agent-side tools must use the Strands `@tool` decorator pattern:

  ```python
  from strands import tool

  @tool
  def example_tool(order_id: str) -> str:
      """One-line description of what this tool does, for the model."""
      ...
  ```

- Prefer Strands' built-in tools (e.g. `current_time` from `strands_tools`) over hand-rolled equivalents when one already covers the need.
- When a tool is backed by an AgentCore Gateway target (Lambda), follow the gateway's MCP client integration pattern as documented for Strands + AgentCore Gateway — look this up via MCP rather than assuming the shape of the request/response.

## AgentCore CLI/config conventions

- After any manual edit to `agentcore.json`, run `agentcore validate` before deploying or relying on the config.
- In `agentcore.json`, runtime `envVars` are an array of objects, not a map:
  ```json
  "envVars": [{ "name": "KEY", "value": "VALUE" }]
  ```
- Treat `agentcore.json` and `.env.local` as the source of truth for resource IDs/ARNs (memory ID, gateway URL, Cognito credentials, etc.) once they've been written there — read from them rather than re-deriving values.

## Scope and style

- Keep agent and tool code minimal: just enough to demonstrate the concept the current lab step is teaching (a single tool, a simple mock, a direct SDK call). Avoid speculative abstractions, extra configuration options, or generalized frameworks.
