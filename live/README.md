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
| `evaluate.py` | Runs a batch of incidents from rendered photos and scores position and door; `--held-out` is the fairer test. |
| `model3d.py` | Prepares each scan's 3D model for the console's 3D route page (`app/live-3d.html`). |
| `examples.py` | Fills the console with ten ready-made incidents to click through, six of them in buildings with more than one level. |

Nothing in `perception/` was changed to make this work. `agent/` has one small change,
in `agent/pinpoint/deep_agent.py`: the LLM run takes an `approve_fn` like the
orchestrator's, saves the model's draft before each approval gate, and reads the
framework's approval requests correctly (it logged every gate as `unknown`). `app/` has
three small changes so the 3D route view opens for any building that has a scan graph
in `app/data/<PropertyId>-scan.json`, not only the two it was built around.

## Quick look, with nothing to type in the console

```sh
.venv/bin/python -m live.serve          # terminal 1
.venv/bin/python -m live.examples       # terminal 2: about a minute per example on a CPU
```

Then open `http://localhost:4175/live.html`. The examples are under **Recent
incidents**, each awaiting review. In an incident, **Follow route** walks the route in
the Matterport scan facing the way the crew walks, and **Open 3D model with route**
shows the whole building as a model with the route drawn through it.

## Buildings

Any building with a graph under `buildings/` works. `env/README.md` describes each.

| Building | Levels in the scan | Why use it |
|---|---|---|
| 6550930, 7900 Fannin St, Houston | 1 (lobby) | 17 health care tenants on record, four doors, and the best door is not the street-address door |
| 10352309, 12814 Willow Centre Dr, Houston | 2 | stairs only: a stretcher cannot reach the upper floor, and the packet says where to leave it |
| 259587, 911 Broxton Ave, Los Angeles | 5 | elevator to every level, including two basements |
| 248351, 3800 Barham Blvd, Los Angeles | 2 | an internal stair; the stretcher goes round by the building elevator |
| princeton-rabinowitz, Rabinowitz Hall, Princeton | 2 | the team can stand in it: stair on foot, elevator for a stretcher. Not in the CoStar kit |
| 234036, 248192, 5956257 | 1 each | an upper-floor suite reached by elevator (two of them) and a stripped floor |

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
- SMS and voice calls. Telegram callers can use the messaging bridges below.

## With a model in charge (`--llm`)

By default the agent's deterministic orchestrator runs the steps. `live.run --llm` runs
lane C's deepagents path instead (`agent/pinpoint/deep_agent.py`): a Commander model and
five subagents decide which tools to call, on the same real tools. It needs
`ANTHROPIC_API_KEY` in the environment and the packages in `agent/requirements.txt`.
Lane C defaults every role to Claude Haiku and stops at a cumulative spend of 5 USD
(`agent/pinpoint/budget.py`, state in `agent/.pinpoint_cost.json`).

```sh
ANTHROPIC_API_KEY=... .venv/bin/python -m live.run --building 6550930 \
    --photo demo/6550930_3ymmrb.jpg --text "collapsed outside the surgery office" --console --llm
```

What the model decides, and what it does not:

- **It decides where the caller is**, by calling the photo match, looking around the scan
  and asking the caller in its own words, and it decides when the packet is ready.
- **It does not write the packet.** When it asks to send, `live/run.py` takes the
  position it reported, uses it only if the photo match returned that position (otherwise
  the best match is used and the packet says the model was overruled), has lane A route
  to it, and writes the packet with `live/packet.py` from records, the scan and the
  graph. The agent's Verifier checks every tagged line, as on the deterministic path.
- **The dispatcher approves that packet**, in the console when one is attached. The
  model's request to contact the building is always declined.
- The packet says which door the model's own report named and whether the routing code
  agrees. What the model itself wrote is kept in `agent/incidents/<id>/llm/`.
- A wrong argument from the model (an address where a building id belongs, a made-up
  position) comes back to it as an error it can read, not a crash.

`live.run --watch --llm` puts the model in charge of incidents started in the console.

What has been run, on 7900 Fannin St on 14 November with a real key (Claude Haiku, lane
C's default): one incident from a rendered caller photo through the console took 139 s,
71 s of it the photo match on a busy CPU, and cost about 0.19 USD. The model chose the
true position and its report named the same door as the routing code; 31 of 31 packet
lines traced to a source. A second incident with the position given took 93 s and
0.18 USD; there the model called `look` about thirty times and `match_photo` with
made-up photo paths before settling on the right position. Two incidents show the path
works end to end. They do not show how often the model is right, and every accuracy
figure below is from the deterministic path. The model path is three to five times
slower than the deterministic one (about 32 s per incident, almost all of it the match).

## Demo walkthrough

1. `.venv/bin/python -m live.serve` in one terminal, `.venv/bin/python -m live.run --watch` in
   another. Open `http://localhost:4175/live.html` (use `localhost`: the Matterport
   viewer key is tied to that name).
2. Search for `7900 Fannin`, select it, and press **Start incident**. Type what the
   caller said and attach `demo/6550930_gbqgw9.jpg` (the lobby seen from near the restrooms) or
   `demo/6550930_3ymmrb.jpg` (at the Suite 1200 door). The other files in `demo/` are
   photos at 7900 Fannin St, 12814 Willow Centre Dr (`10352309`) and 3160 Geneva St
   (`5956257`); the first six characters after the building id are the position.
3. The watcher terminal prints that it is running the agent. In the console, the
   incident fills in: **Destination** and **Entry & route**, the route drawn on the
   floor of the 3D scan from the door the agent chose, **Route on the floor plan** (one
   picture: staging spot, door, path, caller), and under **Photo match evidence** the
   caller's photo beside the matched view with the matched points joined.
4. If the match is ambiguous the agent's question appears in **Caller conversation**.
   Press **Open caller view** and answer it there; the agent updates its belief and
   continues. Without an answer it gives up waiting after 90 seconds and says so. The
   demo photos are all matched with high confidence, so to see the questions run an
   incident with an uncertain match instead:
   `.venv/bin/python -m live.run --building 10352309 --simulate-match e62ehd --console`
5. Switch to **Aerial approach** to see the staging spot and the door on the Vexcel
   image. Press **Open 3D model with route** for the whole building as a model you can
   turn, with the route drawn through the walls. Open **Agent packet** for the full
   packet with its sources.
6. Press **Approve packet locally** or **Reject**. The watcher terminal shows the agent
   finishing with that decision.

The first photo in a session takes longer because the matcher's two models load.

## Measured, and how far to trust it

`.venv/bin/python -m live.evaluate <building> --n <count>` on a laptop CPU with no GPU.
Each incident renders a photo at a random scanned position, runs the whole chain with a
simulated caller, and compares the result with where the photo was taken.

**The plain run flatters the matcher.** The photo is cut from the same panorama the
index holds views of, so the right answer is a near-copy of the question:

| Building | Located at the exact position | Right door |
|---|---|---|
| 7900 Fannin St (lobby, 35 positions) | 14 of 14 | 14 of 14 |
| 3160 Geneva St (one bare floor, 23 positions) | 8 of 8 | 8 of 8 |
| 12814 Willow Centre Dr (two floors, 114 positions) | 8 of 8 | 8 of 8 |

**The held-out run is the one to quote** (`--held-out`). It deletes the photo's own
position from the index first, so the matcher only knows the place from neighbouring
positions one to three meters away, as it would for a caller standing between scanned
spots. The exact position can no longer be the answer; what counts is the distance:

| Building | Within 3 m | Within 5 m | Right floor | Right door |
|---|---|---|---|---|
| 7900 Fannin St | 10 of 12 | 11 of 12 | 12 of 12 | 10 of 12 |
| 12814 Willow Centre Dr | 8 of 12 | 9 of 12 | 11 of 12 | 10 of 12 |
| Rabinowitz Hall, Princeton | 4 of 10 | 8 of 10 | 10 of 10 | 10 of 10 |
| All three | 22 of 34 | 28 of 34 | 33 of 34 | 30 of 34 |

Scanned positions at Rabinowitz Hall are further apart (the nearest one is typically
2.7 m away, against 1.2 m at Willow Centre), so "within 3 m" is a harder bar there.

What the misses looked like:
- At Fannin St one photo, taken at the end of one arm of the cross corridor, was placed
  21 m away in the other arm, which looks the same, at 84% confidence. The agent did not
  ask a question, because it was confident. This is the failure to worry about: wrong,
  sure of itself, and through the wrong door.
- At Willow Centre the three far misses (8 to 12 m) came with 40 to 42% confidence, so
  the packet said "below the confidence threshold" and the questions had been asked. One
  of them was on the wrong floor.
- At Rabinowitz Hall one photo taken at the north stair door was placed at the south
  stair door, 16 m away, at 80% confidence after two questions. The two doors look the
  same and nothing the caller was asked tells them apart.

Still not tested by either run: a different camera, different lighting, people and
furniture that have moved since the scan. Both runs use photos rendered from the scan,
so both are upper bounds on what a real phone photo would do. The only building where
the team can take real photos is Rabinowitz Hall at Princeton: open the console's
caller view on a phone on the same machine's network, or run
`.venv/bin/python -m live.run --building princeton-rabinowitz --photo <file> --console`.
No real photo had been tried when this was written.

Other measurements:
- Time per incident, photo to draft packet: 32 to 45 s on this CPU, nearly all of it
  photo matching. Lane B reports about 2.5 s per photo on a laptop with a GPU.
- A much darker and blurrier photo of the Suite 1200 door (close to a blank frame) was
  matched at 53%; the agent asked three questions (furniture, kind of room, elevator
  doors in view) and ended on the right position at 81%. The current
  `demo/6550930_3ymmrb_hard.jpg` is dim and soft but shows the suite sign, and is
  matched outright.
- A sweep of 31 incidents with the position given, across all seven buildings, had no
  crash, no audit failure, and the same door as `best_entry` in every packet.
- No packet in any run had a line without a source. That check is weaker than it sounds:
  see "What the Verifier does and does not check".

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
| `--llm` | put a model in charge of the tool calls (lane C's deepagents path; needs `ANTHROPIC_API_KEY`). The packet is still written from tool results: see "With a model in charge" |

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

## Evidence court (LangChain deepagents)

The caller is not asked questions (`live.run --watch --ask none --leave-for-review`). When
the photo match is unsure (below 85%) and its top candidates are in different places, the
watcher convenes an evidence court (`live/court.py`, a `create_deep_agent` graph):

1. **Evidence, in parallel.** For each candidate spot (up to three), Claude Haiku 5.5 compares
   the caller's photo with that spot's scanned views and lists matching and conflicting details.
2. **Scouts.** One deepagents subagent per candidate argues that the caller is there, from
   that evidence and the spot's panorama facts, and must name its own weaknesses.
3. **Judge.** The deep agent's lead (Claude Opus 5.5) sends every scout out in one turn,
   weighs the cases, and rules with a typed verdict (`response_format`): winner, confidence,
   the one decisive detail, and a two-sentence ruling.

The ruling reroutes the incident if it names a different spot and appears on the incident
page as **Evidence court**. A first route from the photo match is up before the court sits.
Measured on a Rabinowitz Hall photo: 5 s of evidence, 20 s of deliberation; it read the "1"
on a floor directory sign to separate two identical-looking hallways on floors 1 and 2.

The court runs as a separate process in its own environment so the GPU pipeline is untouched:

```sh
uv venv --python 3.12 .venv-agents
uv pip install --python .venv-agents/bin/python "deepagents>=0.7,<0.8" langchain-anthropic anthropic
```

It needs `ANTHROPIC_API_KEY` in `.env`. Without the environment, or if the court abstains or
times out (`PINPOINT_COURT_TIMEOUT_S`, default 240), the photo match stands. `PINPOINT_COURT=0`
turns it off; `PINPOINT_COURT_JUDGE` / `_SCOUT` / `_EVIDENCE` change the models.
