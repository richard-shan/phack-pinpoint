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
| `demo_photo.py` | Renders a stand-in caller photo from a scanned position (`--hard` for a dark, blurred, zoomed one). |
| `route_image.py` | Draws the route to the caller on the floor plan, with the caller's photo. Every incident gets one as `route.jpg`; `demo/route_*.jpg` are two examples. |
| `evaluate.py` | Runs a batch of incidents from rendered photos and scores position and door. |

Nothing in `agent/` or `perception/` was changed to make this work. `app/` has
three small changes so the 3D route view opens for any building that has a scan graph
in `app/data/<PropertyId>-scan.json`, not only the two it was built around.

## Buildings

Any building with a graph under `buildings/` works: 6550930 (7900 Fannin St, Houston),
10352309, 259587, 234036, 248351, 248192, 5956257. `env/README.md` describes each.
7900 Fannin St is the one to demo: a medical office building with 17 health care
tenants on record, four doors, and a best door that is not the street-address door.

## What the Verifier does and does not check

Every factual packet line carries a `[src:...]` tag, and the agent's Verifier confirms
that the tag points at data that exists in the incident's files (`audit.json`). A line
with no source is moved to "Not confirmed". This catches a claim with nothing behind
it. It does not check that the sentence says what the data says: a sweep of 31 runs
found wording that was wrong or misleading while every line audited as supported.
Those were fixed by hand; the check itself is still only as strong as described here.

## When the agent asks the caller a question

The agent asks only when the answer could change what the crew does: the likely
positions are in different rooms, or knowing which one is right would change the door
or save more than 15 seconds. If the top candidates are a few meters apart in one
room, it does not ask, and the packet says why. The questions come from lane B's
attributes for the candidate positions (room type, floor surface, window, furniture,
stairs or elevator in view) plus what the graph knows (floor, an outside door nearby).

## What is real and what is not

Real, from sponsor data:
- the building record, tenants and risk flags (CoStar);
- the positions, doors, elevators and routes (Matterport scan, marked by hand where the
  scan cannot tell: see `env/MARKING.md`);
- the photo match (lane B's matcher against views cut from the scan);
- what is in view at each position, and what the aerial image shows around the building
  (lane B's vision passes, committed under `perception/data/`; not hand-checked, and
  the packet says so where it uses them);
- the aerial image (Vexcel).

Not real, and labelled in the packet where it applies:
- **The caller photo.** Nobody can stand in these buildings, so demo photos are views
  rendered from the scan (`demo_photo.py`). That is easier than a real phone photo.
- **The caller's answers**, unless someone replies through the console or messaging bridge. With
  `--truth NODE` a simulated caller answers from that position.
- **Travel times.** Speeds and door delays are assumptions in `env/config.py`.
- **Staging spots** are placed by eye on the aerial image.
- **Delivery.** Approval saves an immutable snapshot. With a connected Photon or
  Telegram bridge, that snapshot is sent to the caller; without one it stays local.
  The building contact is never notified.

Not connected yet:
- the agent's LLM path (`agent/pinpoint/deep_agent.py`). `live/` drives the agent's
  deterministic orchestrator; the LLM path uses the same tools but has not been run on
  real data;
- SMS and voice calls. Telegram callers can use the bot bridge below.

## Demo walkthrough

1. `.venv/bin/python -m live.serve` in one terminal, `.venv/bin/python -m live.run --watch` in
   another. Open `http://localhost:4175/live.html` (use `localhost`: the Matterport
   viewer key is tied to that name).
2. Search for `7900 Fannin`, select it, and press **Start incident**. Type what the
   caller said and attach `demo/6550930_gbqgw9_h095.jpg` (taken near the restrooms) or
   `demo/6550930_3ymmrb_h020.jpg` (at the Suite 1200 door). For a photo the matcher is
   unsure about, so that the agent asks questions, use `demo/6550930_3ymmrb_h200_hard.jpg`.
3. The watcher terminal prints that it is running the agent. In the console, the
   incident fills in: **Destination** and **Entry & route**, the route drawn on the
   floor of the 3D scan from the door the agent chose, **Route on the floor plan** (one
   picture: staging spot, door, path, caller), and under **Photo match evidence** the
   caller's photo beside the matched view with the matched points joined.
4. If the match is ambiguous the agent's question appears in **Caller conversation**.
   Press **Open caller view** and answer it there; the agent updates its belief and
   continues. Without an answer it gives up waiting after 90 seconds and says so.
5. Switch to **Aerial approach** to see the staging spot and the door on the Vexcel
   image. Open **Agent packet** for the full packet with its sources.
6. Press **Approve packet locally** or **Reject**. The watcher terminal shows the agent
   finishing with that decision.

The first photo in a session takes longer because the matcher's two models load.

## Measured

`.venv/bin/python -m live.evaluate <building> --n <count>`, on a laptop CPU with no GPU.
Each incident renders a photo at a random scanned position, runs the whole chain with a
simulated caller, and compares the result with where the photo was taken.

| Building | Photos located at the exact position | Right door | Packet lines without a source |
|---|---|---|---|
| 7900 Fannin St (lobby, 35 positions) | 14 of 14 | 14 of 14 | 0 |
| 3160 Geneva St (one bare floor, 23 positions) | 8 of 8 | 8 of 8 | 0 |
| 12814 Willow Centre Dr (two floors, 114 positions) | 8 of 8 | 8 of 8 | 0 |

Time per incident, photo to draft packet: about 32 s, nearly all of it photo matching.
Lane B reports about 2.5 s per photo on a laptop with a GPU.

These photos are rendered from the scan on a scanned position, so the match is easier
than it would be for a real phone photo; treat 30 of 30 as an upper bound. With a
dark, blurred, zoomed photo (`demo/6550930_3ymmrb_h200_hard.jpg`) the matcher was 53%
sure, the agent asked three questions (furniture, kind of room, elevator doors in
view), and it ended on the right position at 81%.

A separate sweep of 31 incidents with the position given, across all seven buildings,
had no crash, no audit failure, and the same door as `best_entry` in every packet.

## Options for `live.run`

| Option | Effect |
|---|---|
| `--building ID --photo FILE` | one incident from a photo |
| `--console [URL]` | show it in the console and wait there for approval |
| `--watch` | run on incidents started in the console that have a caller photo |
| `--truth NODE` | where the photo was taken: a simulated caller answers, and the output says whether the match was right |
| `--position NODE` | the caller's position is known: no matching and no questions |
| `--simulate-match NODE` | stand in for the matcher with an uncertain match around this position, to exercise the agent's questions without the matcher installed |
| `--ask console\|simulate\|none` | who answers the agent's questions |
| `--auto-approve` | do not wait for the dispatcher |

Each run writes `agent/incidents/<id>/` with the agent's files: `intake.json`, `plan.md`,
`brief.json`, `candidates.json`, `location.json`, `site.json`, `route.json`,
`audit.json`, `packet.md`, `route.jpg` (the route on the floor plan), `summary.json`
and `trace.jsonl` (every tool call with its time). A run that could not produce a full
packet prints the reason and exits with status 2.


## Telegram caller channel

`live.telegram` connects private Telegram chats to the same console intake and caller
conversation used by the agent. It uses the [Telegram Bot API](https://core.telegram.org/bots/api)
with polling; no public server or webhook is needed. Create a bot with
[@BotFather](https://t.me/BotFather), then export `TELEGRAM_BOT_TOKEN` in the terminal
that will run the bridge, or put it in the root `.env`, loaded at startup. Optionally set
`TELEGRAM_ALLOWED_CHAT_IDS` as comma-separated numeric private chat IDs; by default
any private chat can use the bot. Group messages are ignored.

Run these in three terminals from the repository root:

```sh
.venv/bin/python -m live.serve
.venv/bin/python -m live.run --watch --ask console
.venv/bin/python -m live.telegram
```

In your bot's Telegram chat:

1. Send a photo **as a photo**, optionally with a caption. Every photo starts a
   separate incident; no `/new` or building selection is required.
2. The bot searches every locally available index for a routable building. Strong,
   distinct geometric evidence creates an incident for the detected building.
   Weak or ambiguous evidence returns **Unknown** and creates no incident.
3. If you already know the building, `/building 6550930` selects it for the **next
   photo only** and bypasses automatic building identification. `/buildings` lists routable IDs;
   some may not yet have a local photo index.
4. Answer follow-up questions in Telegram. Dispatcher messages are forwarded to the
   chat, and text replies appear in the console as caller messages.
5. Text after a photo belongs to that photo until the next photo arrives, including
   while detection runs. It appears under that incident's caller conversation on the
   site once identified. Sending another photo immediately switches subsequent text
   to the new photo, without canceling earlier processing.
6. `/status` checks the latest photo. `/new` clears a pending manual building selection;
   it does not change which photo receives follow-up text or cancel queued work.

Dispatchers review and approve packets in `http://localhost:4175/live.html`.
The bridge sends the immutable approved packet to the originating Telegram chat as a
Markdown document. Rejected drafts and unattended auto-approvals are not delivered.
The watcher must be running for matching and questions. This remains a demo and does
not contact emergency services. See [Photon 2.0 setup and verification](PHOTON.md)
for the sponsor-integrated transport and delivery recovery details.

Use `--console URL` for another local console and `--state PATH` for another state
file. Chat associations, forwarded-message IDs, and the update offset survive restart
in ignored `cache/telegram.json`; bot credentials are not saved there. Run only one
bridge process per bot, with a separate state file for each bot. Existing webhooks
are detected at startup and are not removed automatically. Transient transport errors
are retried without printing credential-bearing URLs. As with most polling bridges,
a crash between a remote write and local state persistence can replay a message;
this is not an exactly-once delivery system.

Validation: `.venv/bin/python -m unittest live.tests.test_telegram -v` runs the real
console server in temporary storage with a fake Telegram transport. A live smoke test
requires your token: send a fresh photo and verify its caption and image in the console,
then send a dispatcher question and answer it in Telegram.


### Automatic building identification

`perception.identify` embeds the photo once and searches all available local building
indexes. For each building it checks the two best views of the top three positions
using LoFTR geometry, one pair at a time to limit GPU memory. Acceptance requires at
least 60 inliers, 0.25 pooled embedding similarity, 50% inliers among raw matches,
4% query-image bounding-box coverage, a combined-score lead of 0.08, and 1.5 times
the strongest competing building's inlier count. These are conservative heuristic
gates, **not calibrated probabilities or a guarantee of identifying arbitrary photos**.
Only buildings with a local index and routing graph can be recognized. A model/index
failure is reported as unavailable, separately from an unknown match.

Identification runs in a single background worker while Telegram polling and message
relay continue. Pending photos survive bridge restarts and are removed after a result
is sent. Multiple photos can queue independently, each retaining its own caption,
text replies, detection result, and incident ID. Dispatcher messages from all linked
incidents are relayed without internal IDs; older incident messages explicitly say
that incoming text still goes to the latest photo. Unknown photos do not enter the
site incident-processing queue, but their text stays in their own saved record and
never falls through to the previous incident. Once a building is identified, the existing watcher
performs the more detailed within-building localization and dispatcher review.

Tests: `.venv/bin/python -m unittest live.tests.test_identify live.tests.test_telegram -v`.

Local smoke checks used four available building indexes: photos from buildings
6550930 and 5956257 identified correctly; the tested photo from 10352309 returned
unknown (31 inliers with little image coverage). An unrelated lobby image, random noise, and a blank image
also returned unknown. Searches took about 24–36 seconds on this laptop, excluding
subsequent within-building processing. This small demo check does not measure the
false-accept rate on real out-of-catalog phone photos.
