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

Needs Python 3.10+ with `requests`, `numpy` and `Pillow` (`pip install -r env/requirements.txt`),
Node.js 20+, and `api.txt` at the repo root.

```sh
python3 -m live.setup                 # once: console catalog, scan graphs, Matterport viewer
python3 -m live.serve                 # terminal 1: the console at http://localhost:4175/live.html
```

Then, in a second terminal, either run one incident from a photo:

```sh
.venv/bin/python -m live.run --building 6550930 --photo demo/6550930_ugmz8k_h300.jpg --console
```

or leave the agent watching the console and start incidents there (pick a building,
"Start incident", attach a caller photo):

```sh
.venv/bin/python -m live.run --watch
```

The console then shows the question to the caller in the conversation, the route drawn
in the 3D scan, and the packet. Approve or reject it there; the agent waits for that.

Photo matching needs lane B's packages and a photo index for the building. `live.setup`
prints the three install commands, then:

```sh
.venv/bin/python -m live.index 6550930        # a few minutes per building on a laptop CPU
```

Without them, everything else still runs if you say where the caller is:

```sh
python3 -m live.run --building 6550930 --position 9nydrb --console --auto-approve
```

`live/README.md` has the details, the buildings, and what is and is not real.

## Tests

```sh
python3 -m unittest discover -s env/tests -t .     # lane A: graphs, routing, console export
python3 -m unittest live.tests.test_live           # all lanes together, with the console
(cd app && npm test)                               # lane D
for t in agent/tests/test_*.py; do python3 $t; done   # lane C
```
