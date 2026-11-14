# Pinpoint — Part C (agent + evaluation lane)

Stub-first scaffolding. Runs **end to end today on fabricated data**, zero dependency on Person A/B
tools or the (locked) CoStar kit. Swaps to real tools one function at a time via the contract.

## Run it

```bash
python3 agent/run_incident.py                 # full stub incident, mock LLM, auto-approve
python3 agent/run_incident.py --interactive   # pause at the approval gate (type y/n)
python3 agent/run_incident.py --real          # real deepagents path (needs install + ANTHROPIC_API_KEY)

python3 agent/tests/test_localization.py       # unit tests (belief math + routing), stdlib only
python3 agent/eval/run_eval.py                 # metrics table (plan §9) over incidents/
```

No dependencies needed for the stub pipeline — it's **stdlib only**. `requirements.txt` is for the
`--real` deepagents path.

## What happens in one run

`run_incident.py` → `orchestrator.run_incident()`:

1. writes `intake.json` + `plan.md` (Commander's todos)
2. runs **Records / Locator / Site in parallel** (threads) — the critical path
   - Locator runs the active-localization loop: `best_question` → `ask_caller` → `update_belief`,
     stopping once confident or after 3 questions
3. **streams `packet.md` section-by-section** as each subagent returns (console sees it fill in)
4. Router computes best door + foot/stretcher routes (needs all three)
5. Verifier checks every `[src:…]` claim; unsupported lines move to "Not confirmed"
6. **approval gate** (interrupt) → `send_packet` on approval
7. `trace.jsonl` logs every tool call with timing (latency profiler + eval input)

## Incident folder (plan §4)

```
agent/incidents/<id>/
  intake.json      address, caller photo, caller text   (given — Person D writes in live mode)
  plan.md          commander todos
  brief.json       CoStar-derived building brief         (Records)
  candidates.json  ranked positions + belief             (Locator)
  location.json    final position, confidence, Q-log     (Locator)
  site.json        entrances, staging, obstructions      (Site)
  route.json       best door, foot+stretcher routes      (Router)
  audit.json       verifier results per claim            (Verifier)
  packet.md        the output (streamed)
  trace.jsonl      every tool call + timing
  summary.json     decision + latencies
```

## Which stubs get replaced, by whom (contract — plan §5)

| Tool | Stub location | Real owner |
|---|---|---|
| `get_property`, `get_tenants` | `pinpoint/stubs.py` | **A** (CoStar) |
| `look`, `entrances` | `pinpoint/stubs.py` | **A** |
| routing: `route`, `best_entry`, `best_entry_under_belief`, `backup_route`, `replan`, `directions`, `exit_route` | `pinpoint/routing.py` (real toy-graph Dijkstra) | **A** (swap `load_graph` data source) |
| `match_photo`, `detect_site_features`, `find_equipment`, `hazards_along`, node attributes | `pinpoint/stubs.py` | **B** (perception) |
| `ask_caller`, `send_packet`, `notify_contact` | `pinpoint/stubs.py` | **D** (Photon + console) |
| `best_question`, `update_belief` | `pinpoint/localization.py` — **REAL already** | **C** (our IP) |
| `check_claim` | `pinpoint/stubs.py` — heuristic, LLM later | **C** |

Signatures are the contract; swapping a stub for a real impl does not change callers.

## Two execution paths (why both exist)

- **`orchestrator.py`** — the runnable spine. Deterministic, stdlib, real thread parallelism, real
  interrupt. This is what runs today and in the demo fallback.
- **`deep_agent.py`** — the real `create_deep_agent(...)` wiring (Commander + 5 subagents, verified
  fields in `DEEPAGENTS_NOTES.md`). Needs `pip install -r requirements.txt` + an API key, and the
  async-subagents API confirmed for true parallelism. Same tool layer → switching is one line.

See `DEEPAGENTS_NOTES.md` for the verified deepagents API and the one unconfirmed gap (parallel
subagents).

## Demo building (fabricated, internally consistent)

Bayou Medical Plaza, 1200 Smith St, Houston — 2 stories, suite 210 = dialysis clinic
(non-ambulatory patients). 15-node toy graph. The **loading-dock door genuinely beats the front
door**, and the **stretcher route genuinely differs** (stairs impassable → elevator). These are the
plan's headline joins, provable from the fixtures.
