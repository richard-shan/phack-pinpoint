# Pinpoint

A 911 caller inside a commercial building sends one photo. Pinpoint finds where in the
building it was taken, and gives the dispatcher a one-page packet for the crew: where to
stage, which door to use, the route to the caller for a crew on foot and for a
stretcher, what is on the way, and who else is in the building.

HackPrinceton F26, CoStar track. Plan: `pinpoint_plan.md`.

## What is in the repo

| Folder | Lane | What it does |
|---|---|---|
| `env/`, `buildings/` | A | Building graphs from the Matterport scans, CoStar records, and all routing |
| `perception/` | B | Matches a photo to a scanned position; reads attributes and aerial imagery |
| `agent/` | C | The agent: plans the incident, asks the caller questions, verifies every line, waits for approval |
| `app/` | D | The dispatcher console: caller conversation, 3D route in the scan, packet review and approval |
| `live/` | all | Runs one incident through all four on real data |

## Run an incident end to end

Needs Node.js 20+, [`uv`](https://docs.astral.sh/uv/), and `api.txt` at the repo root
(it is in the repo). One Python environment runs everything:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r env/requirements.txt
.venv/bin/python -m live.setup        # once: console catalog, scan graphs, Matterport viewer
.venv/bin/python -m live.serve        # terminal 1: the console at http://localhost:4175/live.html
```

That is enough to run an incident where the caller's position is already known. In a
second terminal:

```sh
.venv/bin/python -m live.run --building 6550930 --position 3ymmrb --console --auto-approve
```

The console then shows the route drawn in the 3D scan, the route on the floor plan, and
the packet. Drop `--auto-approve` to make the agent wait for **Approve** or **Reject**
in the console.

To locate the caller from a photo, add lane B's packages and build a photo index for
the building (a few minutes per building on a laptop CPU):

```sh
uv pip install --python .venv/bin/python --index-url https://download.pytorch.org/whl/cpu torch torchvision
uv pip install --python .venv/bin/python -r perception/requirements.txt huggingface_hub safetensors
.venv/bin/python -m live.index 6550930
```

Then run one incident from a photo:

```sh
.venv/bin/python -m live.run --building 6550930 --photo demo/6550930_gbqgw9.jpg --console
```

or leave the agent watching the console and start incidents there (pick a building,
**Start incident**, attach a caller photo):

```sh
.venv/bin/python -m live.run --watch
```

If photo matching is not installed or the building has no index, the run says so in
the terminal and the packet is marked DEGRADED with the reason; it does not pretend to
have located the caller.

By default the agent's deterministic orchestrator runs the steps. To put a model in
charge of the tool calls instead (lane C's deepagents path, Claude Haiku, about two
minutes and 0.20 USD per incident), install `agent/requirements.txt` and add `--llm`:

```sh
uv pip install --python .venv/bin/python -r agent/requirements.txt
ANTHROPIC_API_KEY=... .venv/bin/python -m live.run --building 6550930 --photo demo/6550930_3ymmrb.jpg --console --llm
```

The packet is still written from the tools' results and checked line by line; the
model chooses the position and when to send. See "With a model in charge" in
`live/README.md`.

If port 4175 is taken: `.venv/bin/python -m live.serve --port 4180`, and pass
`--console http://localhost:4180` to `live.run`. Open the console as `localhost`, not
`127.0.0.1`: the Matterport viewer key is tied to that name.

`live/README.md` has the details, the buildings, a demo walkthrough, and what is and is
not real.

## Tests

```sh
.venv/bin/python -m unittest discover -s env/tests -t .     # lane A: graphs, routing, console export
.venv/bin/python -m unittest live.tests.test_live           # all lanes together, with the console
(cd app && npm test)                                        # lane D
for t in agent/tests/test_*.py; do .venv/bin/python $t; done   # lane C
```
