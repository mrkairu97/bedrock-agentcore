# Memory Fix Implementation Plan

## Diagnosis (confirmed, not re-litigated)

`CustomerAssistantMemory` (ARN `arn:aws:bedrock-agentcore:us-west-2:505930914332:memory/AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P`) was created with `strategies: []`. Confirmed both in `agentcore/agentcore.json` and live via
`aws bedrock-agentcore-control get-memory --memory-id AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P --region us-west-2 --no-cli-pager`. With no `USER_PREFERENCE` strategy deployed, there is no long-term-memory namespace for the agent to write preferences into or retrieve from — hence "agent can't remember preferences" even though short-term, in-process history already works via `NullConversationManager`. The fix must deploy a strategy, wire the memory ID into the runtime, and integrate the Strands AgentCore Memory session manager in `main.py`.

## Key API decisions confirmed from docs/source (do not re-derive)

- **`namespaceTemplates` placeholder syntax is `{actorId}` / `{sessionId}`**, a plain Python `str.format()` template evaluated inside `AgentCoreMemorySessionManager.retrieve_customer_context()` (confirmed by reading the installed `bedrock_agentcore/memory/integrations/strands/session_manager.py`, line ~871: `namespace.format(actorId=self.config.actor_id, sessionId=self.config.session_id, memoryStrategyId=...)`). Pass the **raw template string** `"/users/{actorId}/preference/"` as the `retrieval_config` dict key — do NOT pre-substitute it yourself. This also resolves the "common mistake" warning in the task: the triple-brace form (`f"/users/{{{actor_id}}}/preference"`) is wrong for a different reason than expected — it's not just malformed, the API wants the literal `{actorId}` placeholder, not any pre-substituted value at all.
- **Namespace strings must start and end with `/`** (confirmed in the Strands integration docs' "Namespace Patterns" section). Use `/users/{actorId}/preference/` consistently in both `agentcore.json`'s `namespaceTemplates` and `main.py`'s `retrieval_config` key — they must match exactly.
- **`session_manager` and `conversation_manager` are independent constructor params on `strands.agent.Agent`, not mutually exclusive** (confirmed by reading installed `strands/agent/agent.py`): `conversation_manager` controls context-window/history trimming; `session_manager` (a `SessionManager`/`RepositorySessionManager` subclass) controls persistence and LTM retrieval. They compose. The one exclusivity rule in the SDK is `conversation_manager`/`context_manager` vs. a **stateful model** (`agent.py` line 396: raises `ValueError` if `model.stateful` and a conversation_manager is given) — irrelevant here since `BedrockModel.stateful` defaults to `False` (confirmed in `strands/models/model.py`). So the existing `NullConversationManager()` stays, and `AgentCoreMemorySessionManager` is added alongside it as a new `session_manager=` kwarg.
- **No new dependency needed.** `uv run python3 -c "from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig; from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager"` succeeds today from `app/CustomerAssistantAgent` — `bedrock-agentcore[strands-agents]>=1.18.1` already provides it.
- **`AgentCoreMemoryConfig` requires `memory_id`, `session_id`, `actor_id`** (all `str`); `retrieval_config: Dict[str, RetrievalConfig]` maps a namespace template string to a `RetrievalConfig(top_k=..., relevance_score=...)`. Default `batch_size=1` (immediate send, no `with`/`close()` needed) — keep this default since the task says keep code minimal and the existing code has no agent-lifecycle hook to call `close()` from.
- **`AgentCoreMemorySessionManager(agentcore_memory_config=config, region_name=...)`** — region must be passed explicitly (constructor does not read `AWS_REGION`); use `us-west-2` literal/env-driven, matching project convention.
- One session manager per Agent/session, confirmed by both docs ("Session Limitations: only one agent per session is currently supported... Creating multiple agents with the same session will show a warning") — this matches the existing per-session `agent_factory()` cache 1:1 (one `Agent` per `session_id` already), so each cached `Agent` needs its own `AgentCoreMemorySessionManager` built with that session's `session_id`.

## Steps

- [x] 1. Add the `USER_PREFERENCE` memory strategy to `agentcore/agentcore.json`.
      In `memories[0].strategies` (currently `[]`), add one strategy object:
      ```json
      {
        "type": "USER_PREFERENCE",
        "name": "PreferenceLearner",
        "description": "Learns and stores administrator-stated customer preferences (e.g. communication channel, favorite product category) across sessions.",
        "namespaceTemplates": ["/users/{actorId}/preference/"]
      }
      ```
      Use `namespaceTemplates` (current field) — never the deprecated `namespaces`. Leave `eventExpiryDuration: 7` and everything else in the file unchanged.
      Files: `agentcore/agentcore.json`
      Verify: `cd agentcore && agentcore validate --no-cli-pager` is not a real flag on `validate` (it has no `--no-cli-pager` option — that constraint applies only to raw `aws` CLI calls); instead run `agentcore validate` from the `AgentCoreProject` directory and confirm it prints `Valid` with no errors.

- [x] 2. Deploy the strategy to the live AWS memory resource.
      Run `agentcore deploy` from the `AgentCoreProject` directory (not `agentcore/`) to synthesize CDK and apply the updated memory config. This is an in-place update to the existing `CustomerAssistantMemory` resource (same `name`, so no destroy/recreate per `AGENTS.md`'s Resource Identity rule).
      Files: none (deploy only)
      Verify: `aws bedrock-agentcore-control get-memory --memory-id AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P --region us-west-2 --no-cli-pager` and confirm the JSON response's `strategies` array now contains one entry with `name` `PreferenceLearner`/type `USER_PREFERENCE` and a `namespaces` (resolved) entry matching `/users/{actorId}/preference/`, and `status` is `ACTIVE` (or `CREATING` — if `CREATING`, wait and re-run until `ACTIVE` before proceeding).

- [x] 3. Resolve the memory ID and record it in `.env.local`.
      The memory ID (not ARN) is `AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P` (the segment after `memory/` in the ARN) — this was already confirmed via `agentcore status` and the `get-memory` call above and does not change across this deploy (same memory resource, strategy added in place). Re-confirm with `agentcore status` after step 2's deploy completes. Write it to `agentcore/.env.local` (currently empty):
      ```
      AGENTCORE_MEMORY_ID=AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P
      ```
      Files: `agentcore/.env.local`
      Verify: `cat agentcore/.env.local` shows the `AGENTCORE_MEMORY_ID=...` line.

- [x] 4. Add the `envVars` array to the `CustomerAssistantAgent` runtime in `agentcore/agentcore.json`, then re-validate/re-deploy.
      In `runtimes[0]` (`CustomerAssistantAgent`), add (per the project's envVars convention and the `EnvVar` schema in `.llm-context/agentcore.ts`, which requires an array of `{name, value}` objects, not a map):
      ```json
      "envVars": [
        { "name": "AGENTCORE_MEMORY_ID", "value": "AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P" },
        { "name": "AWS_REGION", "value": "us-west-2" }
      ]
      ```
      Files: `agentcore/agentcore.json`
      Verify: run `agentcore validate` from `AgentCoreProject` and confirm `Valid`; then run `agentcore deploy` and confirm it completes without error so the runtime environment is updated with these two vars (confirm via `agentcore status` showing the runtime still `READY`, or `aws bedrock-agentcore-control get-agent-runtime ... --no-cli-pager` showing the env vars in the environment variables section if you want to double check — optional since `agentcore status` already confirms deploy succeeded).

- [x] 5. Integrate `AgentCoreMemorySessionManager` into `app/CustomerAssistantAgent/main.py`.
      Make these changes, preserving every existing piece of functionality (system prompt, all 6 tools incl. the MCP client, `agent_factory()`'s 128-entry LRU cache, `strip_trailing_tool_use`/`_extract_prompt`/`_is_inline_function_call`/`_has_inline_function_call`, and the `@app.entrypoint invoke` streaming logic):

      a. Add imports near the top (after the existing `strands_tools.current_time` import):
         ```python
         import os
         from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
         from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
         ```

      b. Add module-level constants (near `DEFAULT_SYSTEM_PROMPT`), with type hints and an educational comment on the namespace template:
         ```python
         # Admin-facing agent: every session acts on behalf of the same administrator,
         # so actor_id defaults to a fixed value rather than being derived per-request.
         DEFAULT_ACTOR_ID: str = "administrator"
         AWS_REGION: str = os.environ.get("AWS_REGION", "us-west-2")
         AGENTCORE_MEMORY_ID: str | None = os.environ.get("AGENTCORE_MEMORY_ID")

         # Namespace template for the USER_PREFERENCE strategy configured on
         # CustomerAssistantMemory (see agentcore/agentcore.json). This is the raw
         # {actorId} placeholder template -- AgentCoreMemorySessionManager substitutes
         # it internally via str.format(actorId=..., sessionId=...). Do NOT pre-substitute
         # it yourself (e.g. f"/users/{actor_id}/preference/") -- pass the literal template
         # string here, and it must exactly match the strategy's namespaceTemplates entry.
         PREFERENCE_NAMESPACE_TEMPLATE: str = "/users/{actorId}/preference/"
         ```

      c. Add a helper that builds a per-session `AgentCoreMemorySessionManager` (or `None` if memory isn't configured, so local `agentcore dev` without the env var set still runs):
         ```python
         def _make_memory_session_manager(session_id: str) -> AgentCoreMemorySessionManager | None:
             """Build a per-session AgentCore Memory session manager for long-term preference recall.

             Returns None when AGENTCORE_MEMORY_ID is not set so the agent still runs
             locally without a deployed memory resource.
             """
             if not AGENTCORE_MEMORY_ID:
                 log.warning("AGENTCORE_MEMORY_ID not set; running without long-term memory.")
                 return None
             memory_config = AgentCoreMemoryConfig(
                 memory_id=AGENTCORE_MEMORY_ID,
                 session_id=session_id,
                 actor_id=DEFAULT_ACTOR_ID,
                 retrieval_config={
                     # Low threshold (0.3) for testing/demo purposes -- a production
                     # deployment would tune this higher to reduce noisy matches.
                     PREFERENCE_NAMESPACE_TEMPLATE: RetrievalConfig(top_k=5, relevance_score=0.3),
                 },
             )
             return AgentCoreMemorySessionManager(
                 agentcore_memory_config=memory_config,
                 region_name=AWS_REGION,
             )
         ```

      d. In `agent_factory()`'s `get_or_create_agent`, pass `session_manager=_make_memory_session_manager(session_id)` into the `Agent(...)` constructor alongside the existing `conversation_manager=_make_conversation_manager()` (both are independent, non-conflicting kwargs -- see "Key API decisions" above):
         ```python
         cache[session_id] = Agent(
             model=load_model(),
             system_prompt=DEFAULT_SYSTEM_PROMPT,
             tools=tools,
             conversation_manager=_make_conversation_manager(),
             session_manager=_make_memory_session_manager(session_id),
             hooks=[
             ],
         )
         ```

      Files: `app/CustomerAssistantAgent/main.py`
      Verify: `uv run python3 -c "import main"` from `app/CustomerAssistantAgent` succeeds with no import/syntax errors (set a dummy `AGENTCORE_MEMORY_ID` env var first if you want to exercise the memory-manager branch, e.g. `AGENTCORE_MEMORY_ID=test uv run python3 -c "import main"`).

- [x] 6. Redeploy the runtime with the updated `main.py`.
      Run `agentcore deploy` again from `AgentCoreProject` to push the code change (CodeZip build) with the new memory integration live.
      Files: none (deploy only)
      Verify: `agentcore status` shows `CustomerAssistantAgent: Deployed - Runtime: READY`.

- [x] 7. Verify cross-session preference recall end-to-end.
      From the `AgentCoreProject` directory, seed a preference in one one-shot invocation:
      ```
      agentcore dev "Customer C-01, Rajesh Kumar, prefers email communication and his favorite product category is electronics." --runtime CustomerAssistantAgent
      ```
      AgentCore memory extraction (turning a raw conversation turn into a retrievable `USER_PREFERENCE` record) is asynchronous and eventually consistent -- wait at least 30-60 seconds (or poll) before querying. Then, in a **separate** one-shot invocation (a new local session; note each `agentcore dev "<prompt>"` call is a fresh ad hoc session locally, so this already approximates a different session without extra setup):
      ```
      agentcore dev "What are the communication preferences for customer C-01?" --runtime CustomerAssistantAgent
      ```
      Verify: the second response's text reflects "email" and "electronics" rather than a generic "I don't know" / "I don't have that information" answer. This is the actual proof the bug is fixed -- the acceptance bar for this whole task.

      **If recall fails**, work through these fallback diagnostics before concluding the integration is broken:
      - Confirm the `USER_PREFERENCE` strategy actually finished processing the seed turn into a memory record: `aws bedrock-agentcore-control list-memory-records --memory-id AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P --namespace "/users/administrator/preference/" --region us-west-2 --no-cli-pager` (substitute the real resolved namespace, i.e. `administrator` for `{actorId}`) and check whether any records exist yet; if empty, wait longer and retry -- extraction is not instantaneous.
      - Check IAM: `aws bedrock-agentcore-control get-memory --memory-id AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P --region us-west-2 --no-cli-pager` and confirm `memoryExecutionRoleArn` is populated and that role has the AgentCore-managed permissions (it's created by `agentcore deploy`'s CDK stack, so a successful deploy in step 2 should already grant this -- only worth re-checking if recall still fails after confirming records exist).
      - Confirm the namespace used in `retrieval_config` (`/users/{actorId}/preference/`) is byte-for-byte identical to the strategy's `namespaceTemplates[0]` in the deployed memory (re-run the step 2 `get-memory` command and compare).
      - Note the local-session limitation: `agentcore dev "<prompt>"` one-shot invocations each get their own ephemeral `session_id`, but because `actor_id` is fixed to `"administrator"` for all sessions (by design, since this is an admin-facing agent), long-term preference memory is actor-scoped and correctly shared across these "different" sessions -- this is the intended behavior being tested, not a workaround.

## Verification

All seven steps were executed and the fix was confirmed working end-to-end.

**Step 1 -- Add strategy + validate**
- Edited `agentcore/agentcore.json`: added `USER_PREFERENCE` strategy `PreferenceLearner` with `namespaceTemplates: ["/users/{actorId}/preference/"]` to `memories[0].strategies` (was `[]`).
- `cd agentcore && agentcore validate` → `Valid`.

**Step 2 -- Deploy strategy to live memory**
- `cd AgentCoreProject && agentcore deploy` → `✓ Deploy to AWS Complete` (6/6).
- `aws bedrock-agentcore-control get-memory --memory-id AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P --region us-west-2 --no-cli-pager` → `strategies` now contains one entry: `strategyId: PreferenceLearner-aO57e46MOm`, `type: USER_PREFERENCE`, `namespaces`/`namespaceTemplates: ["/users/{actorId}/preference/"]`, `status: ACTIVE`.

**Step 3 -- Record memory ID**
- `agentcore status` → `CustomerAssistantMemory: Deployed (USER_PREFERENCE)` with ARN ending `memory/AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P`.
- Wrote `agentcore/.env.local`: `AGENTCORE_MEMORY_ID=AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P`.

**Step 4 -- Runtime envVars**
- Added to `runtimes[0].envVars` in `agentcore.json`: `AGENTCORE_MEMORY_ID` and `AWS_REGION=us-west-2` (array of `{name, value}` objects, not a map).
- `agentcore validate` (from `agentcore/`) → `Valid`.

**Step 5 -- `main.py` integration**
- Verified APIs against both docs via MCP before coding:
  - `awslabs.aws-documentation-mcp-server::read_documentation` on `https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/strands-sdk-memory.html`
  - `strands-agents::fetch_doc` on `https://strandsagents.com/docs/integrations/session-managers/agentcore-memory/`
  - Both confirmed: `AgentCoreMemoryConfig(memory_id, session_id, actor_id, retrieval_config={namespace_template: RetrievalConfig(top_k, relevance_score)})`; the namespace key must be the raw `{actorId}` template (not pre-substituted); `AgentCoreMemorySessionManager(agentcore_memory_config=..., region_name=...)`; default `batch_size=1` needs no `close()`.
  - Also ran `uv run python3 -c "import inspect; from strands.agent.agent import Agent; print(inspect.signature(Agent.__init__))"` to confirm `conversation_manager` and `session_manager` are independent, non-conflicting constructor kwargs.
- Added imports, `DEFAULT_ACTOR_ID`/`AWS_REGION`/`AGENTCORE_MEMORY_ID`/`PREFERENCE_NAMESPACE_TEMPLATE` constants, `_make_memory_session_manager(session_id)` helper, and wired `session_manager=_make_memory_session_manager(session_id)` into `agent_factory()`'s `Agent(...)` construction, alongside the existing `conversation_manager=_make_conversation_manager()`.
- All existing tools, `DEFAULT_SYSTEM_PROMPT`, the 128-entry LRU agent cache, and the `strip_trailing_tool_use`/`_extract_prompt`/`_has_inline_function_call`/`_is_inline_function_call`/`invoke` logic were left untouched.
- `uv run python3 -c "import main"` (from `app/CustomerAssistantAgent`) → succeeded with no output/errors, both without and with `AGENTCORE_MEMORY_ID=test` set.
- No `pyproject.toml` change needed -- `bedrock-agentcore[strands-agents]>=1.18.1` already present and the import check passed with the existing dependency set.

**Step 6 -- Redeploy runtime**
- `agentcore deploy -y` (non-interactive; plain `agentcore deploy` requires a TTY) → `✓ Deployed to 'default'`, runtime ARN `arn:aws:bedrock-agentcore:us-west-2:505930914332:runtime/AgentCoreProject_CustomerAssistantAgent-3eyUZ39OEX`, memory ARN unchanged.

**Step 7 -- Cross-session recall test**
- Started a local dev server (`agentcore dev --logs`, port 8080).
- Seed invocation: `agentcore dev "Customer C-01, Rajesh Kumar, prefers email communication and his favorite product category is electronics."` → agent acknowledged the preference, no exceptions.
- Waited 60s for asynchronous memory extraction.
- Recall invocation (separate one-shot session): `agentcore dev "What are the communication preferences for customer C-01?"` → response: *"the communication preferences for customer C-01 (Rajesh Kumar) are: **Preferred Communication Channel:** Email"* -- correctly reflects the seeded preference, not a generic "I don't know" answer.
- Root-cause-level confirmation (not just a plausible-looking response): `aws bedrock-agentcore list-memory-records --memory-id AgentCoreProject_CustomerAssistantMemory-gqfbPw4X3P --namespace "/users/administrator/preference/" --region us-west-2 --no-cli-pager` returned two persisted `BASE` records under the `PreferenceLearner-aO57e46MOm` strategy: `"Prefers email communication"` and `"Favorite product category is electronics"` -- proving the record came from the deployed long-term memory strategy, not just in-process conversation history.
- Stopped the local dev server afterward; confirmed no lingering process left on port 8080.
- **Acceptance bar met**: the agent now remembers preferences across separate sessions, which was the reported bug.
