# Spectrum iMessage bridge

This folder connects Photon Spectrum Cloud iMessage to the Pinpoint console and agent.
A caller texts the project's iMessage line, then sends a JPEG, PNG, or WebP under 2 MB.
The bridge forwards the photo and earlier text to `POST /api/demo-photo`, which checks
the image and suggests one of the two supported demo buildings. The console saves a
review incident; the separate `live.run --watch` process picks up its photo and runs
DeepAgents with deterministic fallback. Dispatcher questions and messages are sent
back to that conversation while the bridge is running.
An open dispatcher console switches to the incident when the photo arrives; if the
dispatcher is editing, a link appears instead. The route appears after the agent
calculates it, and may take several minutes.

The default photo matcher knows only the indexed Fannin and Los Angeles demo scans.
Uncertain matches do not create an incident; the bridge asks for another supported
demo photo. The dispatcher must verify the suggested building and route. The matcher
can take several minutes on a CPU and needs the indexes described below. This is a
local demo, not an emergency service, and it does not infer phone location.

## Setup

From the repository root:

1. Set up the console and Python environment as described in the main README. Install
   `agent/requirements.txt` for DeepAgents and configure its API key, or the watcher
   uses its deterministic fallback.
2. Install the photo matcher and build both indexes: `.venv/bin/python -m live.index
   6550930 5956257` (see `live/README.md`).
3. Run `cd spectrum && npm install`, then return to the repository root.
4. Create `spectrum/.env` from `spectrum/.env.example` and fill in the Photon project
   credentials. The previous standalone bridge's credentials are not copied here.
5. Stop any existing console and watcher, then run `.venv/bin/python -m
   live.spectrum_demo`. Open <http://localhost:4175/live.html> and text the Photon
   project line. Ctrl+C stops the three demo processes.

For a known-building stage demo such as Princeton, set
`CONSOLE_PROPERTY_ID=princeton-rabinowitz` in `spectrum/.env`. This bypasses building
inference; it is a demo configuration, not evidence of the sender's location. That
building still needs its photo index for the agent to locate the caller. An optional
`CONSOLE_INCIDENT_ID` pins all chats to one incident and should only be used for a
single test conversation.

Alternatively run `.venv/bin/python -m live.serve`, `.venv/bin/python -m live.run
--watch`, and `npm run start` from `spectrum/` in three terminals. Existing linked
conversations are reconnected for outbound replies when they next send a message
following a bridge restart. Local `.bridge-state.json` contains chat links and
pending text; it is private demo state, not durable production storage. The bridge
is iMessage only; it does not provide Telegram integration.
