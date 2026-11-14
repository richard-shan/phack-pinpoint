# deepagents API — verified notes (Part C)

Fetched 2026-11-14 from:
- https://docs.langchain.com/oss/python/deepagents/overview
- https://docs.langchain.com/oss/python/deepagents/subagents
- https://docs.langchain.com/oss/python/deepagents/human-in-the-loop

## `create_deep_agent(...)` params (CONFIRMED present; some detail thin)
- `model`            e.g. `"anthropic:claude-sonnet-5"` (use a model you have keys for)
- `tools`            list of callables
- `system_prompt`    str
- `subagents`        list of subagent dicts (plural) — see below
- `middleware`       list (e.g. `TodoListMiddleware()` for `write_todos`; planning is opt-in from v0.7)
- `interrupt_on`     dict: tool name -> True | False | {"allowed_decisions":[...]}
- `checkpointer`     REQUIRED for interrupts (e.g. `MemorySaver`)
- `memory`           memory files for persistent context (format not fully documented)
- `permissions`      filesystem permission rules

## Subagent definition — dict fields (CONFIRMED)
| field            | type              | notes |
|------------------|-------------------|-------|
| `name`           | str               | id the lead uses to delegate |
| `description`    | str               | action-oriented role blurb |
| `system_prompt`  | str               | required for isolated mode |
| `tools`          | list[Callable]    | overrides inherited tools |
| `model`          | str/BaseChatModel | optional override `'provider:model'` |
| `middleware`     | list              | optional |
| `interrupt_on`   | dict              | optional per-subagent HITL |
| `skills`         | list[str]         | optional |
| `response_format`| ResponseFormat    | optional structured output |
| `permissions`    | list              | optional |
| `mode`           | "isolated"/"fork" | context inheritance |

Built-in tools (no need to define): `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `task`.

## Human-in-the-loop / interrupts (CONFIRMED)
```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

checkpointer = MemorySaver()
agent = create_deep_agent(..., interrupt_on={"send_packet": True, "notify_contact": True},
                          checkpointer=checkpointer)

config = {"configurable": {"thread_id": "<incident_id>"}}
result = agent.invoke({"messages":[{"role":"user","content": "..."}]}, config=config)

if result.get("__interrupt__"):
    req = result["__interrupt__"][0].value["action_requests"]
    decisions = [{"type": "approve"}]            # approve | edit | reject | respond
    result = agent.invoke(Command(resume={"decisions": decisions}), config=config)
```
Decision types: `approve` (run unchanged), `edit` (modify args), `reject` (skip + feedback),
`respond` (synthetic tool result, for ask-user tools). Decisions must match `action_requests` order.

## PARALLEL subagents — confirmed: needs Agent Server, not a dict flag
- Dict-based subagents are **synchronous**: the supervisor BLOCKS until each returns.
- True async is `AsyncSubAgent` + Agent Protocol / Agent Server (`start_async_task`, etc.) —
  see https://docs.langchain.com/oss/python/deepagents/async-subagents.md. Not a field on
  ordinary subagent dicts. Overkill for the local hackathon path.

### Consequence for Part C
Records/Locator/Site parallelism lives in `orchestrator.py` (ThreadPoolExecutor + as_completed).
`deep_agent.py` holds the real `create_deep_agent(...)` wiring for the agentic demo path.
Both call the SAME tool layer; swap is one line in run_incident.py.

---

## Install note (2026-11-14, this session)

- deepagents requires **Python >=3.11**. The sandbox this fork ran in is Python 3.10, so the
  live install was blocked here, not by the egress proxy. On a 3.11+ machine the pinned
  `agent/requirements.txt` set installs cleanly.
- `api.txt` does NOT contain an Anthropic key (only Matterport + Vexcel keys). Set
  `ANTHROPIC_API_KEY` in env, or add a line `anthropic_api_key: sk-ant-...` to `api.txt`
  before running with `--real-llm`. `deep_agent._load_api_key_from_file` accepts either
  `anthropic_api_key:` or `ANTHROPIC_API_KEY=` and falls back to any bare `sk-ant-*` token.
- `TodoListMiddleware` import path varies by deepagents version. `build_agent()` probes both
  `deepagents.middleware.TodoListMiddleware` and `deepagents.TodoListMiddleware` and no-ops
  the middleware list if neither is found. Confirm the real path on the installed version.
