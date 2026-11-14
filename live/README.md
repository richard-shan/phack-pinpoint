# live: one incident through all four lanes

`agent/` runs end to end on one made-up building. `live/` runs the same agent on real
data by replacing its tools, and shows the result in the console.

```
caller photo ──> match_photo (perception/) ──> ranked positions
                                                    │
address ──> get_property (env/, CoStar) ──┐         ▼
                                          ├──> agent/ orchestrator: question loop, best door
entrances, staging (env/, graph) ─────────┘    under uncertainty, routes, verifier
                                                    │
                          console (app/): question to the caller, route in the 3D scan,
                          packet, dispatcher approves or rejects  <──┘
```

## Files

| File | What it does |
|---|---|
| `tools.py` | Real versions of every tool the agent calls, with the same names and signatures. `install()` swaps them in for `agent/pinpoint/stubs.py` and `agent/pinpoint/routing.py`. |
| `packet.py` | The responder packet for real buildings. Every factual line keeps a `[src:...]` tag for the agent's Verifier. Each update is also pushed to the console. |
| `console_api.py` | Client for the console's local HTTP service. |
| `run.py` | Run one incident, or watch the console for incidents (`--watch`). |
| `setup.py` | Console data from what is in the repo: catalog, scan graphs, Matterport viewer. |
| `serve.py` | Starts the console with the keys from `api.txt` (in memory only). |
| `index.py` | Builds lane B's photo index for the scan a lane A graph covers. |
| `demo_photo.py` | Renders a stand-in caller photo from a scanned position. |

Nothing in `agent/`, `perception/` or `env/` was changed to make this work. `app/` has
three small changes so the 3D route view opens for any building that has a scan graph
in `app/data/<PropertyId>-scan.json`, not only the two it was built around.

## Buildings

Any building with a graph under `buildings/` works: 6550930 (7900 Fannin St, Houston),
10352309, 259587, 234036, 248351, 248192, 5956257. `env/README.md` describes each.
7900 Fannin St is the one to demo: a medical office building with 17 health care
tenants on record, four doors, and a best door that is not the street-address door.

## What is real and what is not

Real, from sponsor data:
- the building record, tenants and risk flags (CoStar);
- the positions, doors, elevators and routes (Matterport scan, marked by hand where the
  scan cannot tell: see `env/MARKING.md`);
- the photo match (lane B's matcher against views cut from the scan);
- the aerial image (Vexcel).

Not real, and labelled in the packet where it applies:
- **The caller photo.** Nobody can stand in these buildings, so demo photos are views
  rendered from the scan (`demo_photo.py`). That is easier than a real phone photo.
- **The caller's answers**, unless someone types them in the console's caller view. With
  `--truth NODE` a simulated caller answers from that position.
- **Travel times.** Speeds and door delays are assumptions in `env/config.py`.
- **Staging spots** are placed by eye on the aerial image.
- **Delivery.** Approval is recorded in the console. Nothing is sent to anyone, and the
  building contact is never notified.

Not connected yet:
- the agent's LLM path (`agent/pinpoint/deep_agent.py`). `live/` drives the agent's
  deterministic orchestrator; the LLM path uses the same tools but has not been run on
  real data;
- lane B's per-position attributes (`perception/attributes.py`) and aerial reading
  (`perception/site.py`). Both need an Anthropic API key. The agent's questions use what
  the graph knows instead: floor, room name, and whether an elevator, an outside door
  or stairs is within sight;
- a real phone channel. The caller types in the console's caller view.

## Demo walkthrough

1. `python3 -m live.serve` in one terminal, `.venv/bin/python -m live.run --watch` in
   another. Open `http://localhost:4175/live.html` (use `localhost`: the Matterport
   viewer key is tied to that name).
2. Search for `7900 Fannin`, select it, and press **Start incident**. Type what the
   caller said and attach `demo/6550930_gbqgw9_h095.jpg` (taken near the restrooms) or
   `demo/6550930_3ymmrb_h020.jpg` (at the Suite 1200 door).
3. The watcher terminal prints that it is running the agent. In the console, the
   incident fills in: **Destination** and **Entry & route**, the route drawn on the
   floor of the 3D scan from the door the agent chose, and under **Photo match
   evidence** the caller's photo beside the matched view with the matched points joined.
4. If the match is ambiguous the agent's question appears in **Caller conversation**.
   Press **Open caller view** and answer it there; the agent updates its belief and
   continues. Without an answer it gives up waiting after 90 seconds and says so.
5. Switch to **Aerial approach** to see the staging spot and the door on the Vexcel
   image. Open **Agent packet** for the full packet with its sources.
6. Press **Approve packet locally** or **Reject**. The watcher terminal shows the agent
   finishing with that decision.

The first photo in a session takes longer because the matcher's two models load.

## Options for `live.run`

| Option | Effect |
|---|---|
| `--building ID --photo FILE` | one incident from a photo |
| `--console [URL]` | show it in the console and wait there for approval |
| `--watch` | run on incidents started in the console that have a caller photo |
| `--truth NODE` | where the photo was taken: a simulated caller answers, and the output says whether the match was right |
| `--position NODE` | skip photo matching and use this position |
| `--ask console\|simulate\|none` | who answers the agent's questions |
| `--auto-approve` | do not wait for the dispatcher |

Each run writes `agent/incidents/<id>/` with the agent's files: `brief.json`,
`candidates.json`, `location.json`, `site.json`, `route.json`, `audit.json`, `packet.md`
and `trace.jsonl` (every tool call with its time).
