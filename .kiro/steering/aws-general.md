---
inclusion: always
---

# AWS General Practices

These rules apply to all AWS-related work in this project: writing code that calls AWS services, running AWS CLI commands, and designing infrastructure.

## Region

- All AWS operations — CLI commands, SDK/boto3 clients, Strands model configuration, AgentCore resources — target **us-west-2** unless the user explicitly says otherwise for a specific task.
- Never hardcode a different region as a "default" or leave region unset and rely on environment fallback. Pass or configure `us-west-2` explicitly in code and commands.

## AWS CLI usage

- Every AWS CLI command run from a terminal MUST include the `--no-cli-pager` option. This applies to every `aws ...` invocation, not just ones that are expected to return long output.
  - Example: `aws dynamodb scan --table-name workshop-orders --region us-west-2 --no-cli-pager`

## Best practices and documentation accuracy

- Follow AWS best practices (security, least-privilege IAM, resource naming, error handling) in all code and infrastructure changes.
- Do not answer AWS questions or write AWS-related code from memory alone. AWS APIs, CLI flags, console workflows, and service limits change often enough that memory can be stale or wrong.
- Before writing code or commands that touch an AWS service, or answering a question about AWS behavior/limits/APIs, use the `awslabs.aws-documentation-mcp-server` MCP tools (`search_documentation`, `read_documentation`, `read_sections`, `search_table`, `recommend`) to confirm current, official guidance. Cite the doc URL used.
- If the MCP documentation tools are unavailable in a session, say so explicitly rather than silently falling back to memory, and flag that the answer/code should be re-verified once the tools are available.

## Scope and style

- Keep AWS infrastructure and integration code minimal and focused on demonstrating the concept at hand. Avoid adding extra resources, abstractions, or defensive handling beyond what the current task needs.
