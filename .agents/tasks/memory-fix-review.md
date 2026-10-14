# Review: AgentCore memory-recall fix (deploy USER_PREFERENCE strategy + session manager)

The task was to fix "agent can't remember preferences" by deploying a `USER_PREFERENCE` memory strategy, wiring the resulting memory ID through `.env.local` and `agentcore.json`'s runtime `envVars`, and integrating `AgentCoreMemorySessionManager` into `main.py` so each per-session cached `Agent` gets long-term preference recall. The diff touches `agentcore/agentcore.json`, `agentcore/.env.local`, `agentcore/aws-targets.json`, `agentcore/.cli/deployed-state.json`, `app/CustomerAssistantAgent/main.py`, `pyproject.toml`, and `uv.lock`.

**Watch for:** none — every item in the task's checklist (namespaceTemplates vs deprecated namespaces, envVars array shape, no triple-brace mistake, region, preserved tools/helpers/LRU, session_manager + conversation_manager coexistence) checked out against the installed SDK source and the live diff.

**Verdict**: APPROVED

## High-level view

The root cause — `CustomerAssistantMemory` deployed with `strategies: []` — is fixed by adding a `USER_PREFERENCE` strategy named `PreferenceLearner` with `namespaceTemplates: ["/users/{actorId}/preference/"]` to `agentcore.json`. This is the current, non-deprecated field; the schema in `.llm-context/agentcore.ts` confirms `namespaces` is marked `@deprecated` in favor of `namespaceTemplates`.

The memory ID is pulled from the live resource (not hardcoded as a literal elsewhere) and recorded once in `agentcore/.env.local`, then threaded into `runtimes[0].envVars` as the required array-of-objects shape (`AGENTCORE_MEMORY_ID`, `AWS_REGION=us-west-2`), matching the schema and the steering convention.

`main.py` reads `AGENTCORE_MEMORY_ID` via `os.environ.get`, builds one `AgentCoreMemorySessionManager` per cached session inside `agent_factory()`, and passes it alongside the existing `conversation_manager=NullConversationManager()`. Both diff and installed `strands/agent/agent.py` source confirm these two kwargs are independent — the only conflict Strands enforces is `conversation_manager` vs. a stateful model, and `BedrockModel.stateful` is `False` here, so no conflict exists.

The namespace template passed to `retrieval_config` is the literal `/users/{actorId}/preference/` string — not pre-substituted — matching how `AgentCoreMemorySessionManager.retrieve_customer_context()` resolves it internally via `namespace.format(actorId=..., sessionId=...)` (confirmed by reading the installed `session_manager.py`). The known triple-brace mistake (`f"/users/{{{actor_id}}}/preference"`) does not appear anywhere in the diff.

All pre-existing tools (`add_numbers`, MCP client), the new tools from the prior task (`order_lookup`, `user_lookup`, `product_lookup`, `policy_retrieval`, `current_time`), the system prompt, the 128-entry LRU agent cache, and the `strip_trailing_tool_use`/`_extract_prompt`/`_has_inline_function_call`/`_is_inline_function_call`/`invoke` block are present and byte-identical to the base version — confirmed by diffing the base commit's `main.py` against the tail of the current file.

<details>
<summary>Issues (0)</summary>

No blocking or non-blocking findings.

</details>

<details><summary>Details</summary>

### USER_PREFERENCE strategy and namespace template

`agentcore.json`'s `memories[0].strategies` now contains one entry:

```json
{
  "type": "USER_PREFERENCE",
  "name": "PreferenceLearner",
  "description": "...",
  "namespaceTemplates": ["/users/{actorId}/preference/"]
}
```

This uses `namespaceTemplates`, not the deprecated `namespaces` field (verified against the `MemoryStrategy` interface in `agentcore/.llm-context/agentcore.ts`, which marks `namespaces` `@deprecated`). The template string uses the literal `{actorId}` placeholder, not Python f-string braces, and starts/ends with `/` per the Strands docs' "Namespace Patterns" section.

### envVars shape and region

`runtimes[0].envVars` is an array of `{name, value}` objects:

```json
"envVars": [
  { "name": "AGENTCORE_MEMORY_ID", "value": "AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P" },
  { "name": "AWS_REGION", "value": "us-west-2" }
]
```

This matches the `EnvVar` interface in `.llm-context/agentcore.ts` (`envVars?: EnvVar[]`) rather than a map, and matches the steering rule added in message 7 of the task history. `agentcore/.env.local` records the memory ID (confirmed to be the ID segment after `memory/` in the ARN, not the ARN itself) as `AGENTCORE_MEMORY_ID=AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P`. No region other than `us-west-2` appears anywhere in the diff (grepped `main.py`, `agentcore.json`, `.env.local` for `us-east-1`/`us-east-2`/`us-west-1` — no matches).

### session_manager vs. conversation_manager coexistence

The plan claims these are independent, non-conflicting `Agent.__init__` kwargs, and that the one exclusivity rule Strands enforces is `conversation_manager`/`context_manager` vs. a stateful model. Reading the installed `strands/agent/agent.py`:

```python
if self.model.stateful and (conversation_manager is not None or context_manager not in (None, False)):
    raise ValueError(
        "context_manager and conversation_manager cannot be used with a stateful model. "
    )
```

`model/load.py` uses `BedrockModel`, which does not override the base `Model.stateful` property (`strands/models/model.py` returns `False` by default, and `strands/models/bedrock.py` has no `stateful` override). So this check never fires here, and `session_manager=_make_memory_session_manager(session_id)` is correctly added alongside the pre-existing `conversation_manager=_make_conversation_manager()` without conflict.

### Retrieval config matches the installed SDK's substitution mechanism

`main.py` passes `PREFERENCE_NAMESPACE_TEMPLATE = "/users/{actorId}/preference/"` as the raw key in `retrieval_config`, not pre-substituted with the Python `actor_id` variable. Reading the installed `bedrock_agentcore/memory/integrations/strands/session_manager.py`:

```python
resolved_namespace = namespace.format(
    actorId=self.config.actor_id,
    sessionId=self.config.session_id,
    memoryStrategyId=retrieval_config.strategy_id or "",
)
```

confirms the session manager does its own `.format()` substitution against the literal template — passing a pre-substituted string (the specifically called-out common mistake, `f"/users/{{{actor_id}}}/preference"`) would either fail to resolve or produce a mismatched namespace. That mistake does not appear in the diff; a search for the triple-brace pattern and for `preference"` / `preference/` across `main.py` turns up only the correct literal template and the explanatory comment describing why not to pre-substitute.

### Preservation of existing agent behavior

Diffing the initial commit's `main.py` against the current file shows the system prompt, all five mock-data tools, the MCP client wiring, the `agent_factory()` LRU cache (128-entry cap, `move_to_end`/`popitem(last=False)`), and the four helper functions plus the `@app.entrypoint invoke` streaming loop are unchanged — the diff is purely additive (new imports, new constants, the new helper, and one new `session_manager=` kwarg in the `Agent(...)` call). `uv run python3 -c "import main"` with `AGENTCORE_MEMORY_ID=test` set succeeds in this environment, confirming no import/syntax regression. The new code carries type hints throughout (`DEFAULT_ACTOR_ID: str`, `AGENTCORE_MEMORY_ID: str | None`, `_make_memory_session_manager(session_id: str) -> AgentCoreMemorySessionManager | None`) and the comments on the namespace template and actor_id default explain the non-obvious AgentCore-specific reasoning rather than restating the code, per the project's steering rules. `pyproject.toml`'s `strands-agents-tools` addition is unrelated to the memory integration itself (it backs the pre-existing `current_time` import) — no new dependency was needed for `AgentCoreMemoryConfig`/`AgentCoreMemorySessionManager`, since `bedrock-agentcore[strands-agents]` already provided them.

### Verification evidence

The plan document's Verification section and the commit message both record the full command sequence: `agentcore validate` after each `agentcore.json` edit, `agentcore deploy`/`agentcore deploy -y`, `aws bedrock-agentcore-control get-memory ... --region us-west-2 --no-cli-pager` (strategy `ACTIVE`), the `uv run python3 -c "import main"` check, the seed invocation (`agentcore dev "Customer C-01, Rajesh Kumar, prefers email communication..."`), a 60-second wait for asynchronous extraction, and the recall invocation (`agentcore dev "What are the communication preferences for customer C-01?"`) returning a response that names "email" and references the seeded preference rather than a generic "I don't know." The plan goes a step further than the bare acceptance bar by also running `aws bedrock-agentcore list-memory-records --memory-id ... --namespace "/users/administrator/preference/" --region us-west-2 --no-cli-pager`, which returned two persisted `BASE` records under strategy `PreferenceLearner-aO57e46MOm` — this ties the observed response directly to the deployed long-term memory store rather than resting on the agent's response text alone. Every `aws` CLI command recorded includes `--no-cli-pager`.

No file in this diff was unrelated to the task; `aws-targets.json` and `.cli/deployed-state.json` are expected side effects of `agentcore deploy` populating the AWS account/region target and the deployed resource IDs/ARNs, not scope creep.

</details>

---

Full diff: `git diff d3cebb6 HEAD` in `/home/workshop/ReturnsRefundsAgentProject/AgentCoreProject`.

<details>
<summary>File map</summary>

- `agentcore/agentcore.json` — added `USER_PREFERENCE` strategy to the memory resource; added `envVars` array to the runtime.
- `agentcore/.env.local` — recorded `AGENTCORE_MEMORY_ID`.
- `agentcore/aws-targets.json` — populated by `agentcore deploy` with the account/region target (side effect, not hand-edited scope).
- `agentcore/.cli/deployed-state.json` — populated by `agentcore deploy` with runtime/memory IDs and ARNs (side effect).
- `app/CustomerAssistantAgent/main.py` — added memory imports/constants, `_make_memory_session_manager()`, and wired `session_manager=` into `agent_factory()`'s `Agent(...)` call; all prior tools/prompt/cache/helpers preserved.
- `app/CustomerAssistantAgent/pyproject.toml` — added `strands-agents-tools` dependency.
- `app/CustomerAssistantAgent/uv.lock` — relocked for the new dependency.
- `.agents/tasks/memory-fix-plan.md` — implementation plan and verification log (not application code).

</details>
