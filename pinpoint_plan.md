# Pinpoint: build plan (HackPrinceton F26)

Written Saturday 1:15 PM. Submit by Sunday 7:30 AM, hard deadline 8:00 AM. About 18 hours of build time.

**Reading I took.** "The first responders thing" = Pinpoint: find where a caller is inside a building from a photo, then hand responders a packet that tells them which door, which route, and what is in the way. Pre-incident plans (the Pre-Plan idea) run on the same pipeline as a batch mode. The Beeline AED beacon is a stretch item only. If you meant Beeline as the core, sections 4 to 7 still apply and section 3 changes.

**Split.** Four lanes, A to D. If you are five people, see "Fifth person" in section 8.

---

## 1. The idea in three sentences

A 911 call from a large commercial building gives dispatch a street address. It does not give the floor, the suite, or the door that is closest to the patient. Pinpoint takes one photo texted by the caller, matches it to a position in the building's digital twin, and produces a responder packet: where to stage, which door to use, the route to the room, and what the crew will meet on the way.

**The line for judges.** "The address points to the front door. The patient is 40 meters from the loading dock door. No single dataset knows that."

**Who it is for.** Dispatchers and the first-arriving crew. Secondary user: fire departments that write pre-incident plans by hand, one building at a time.

---

## 2. Scoring against CoStar's rubric

Rubric from the packet: 30% use of the data, 30% agentic depth, 25% usefulness, 15% demo and polish. Hard rules: at least 2 of 3 layers, Houston and LA buildings only, and it must act, not chat.

| Criterion | Weight | What we show | Owner |
|---|---|---|---|
| Use of the data | 30% | All three layers, and each answers a question the other two cannot. Facts are derived from pixels, not displayed next to them. Every line in the packet links to its source record, panorama crop, or aerial tile. | A, B |
| Agentic depth | 30% | A lead agent plans, hands work to four subagents, asks the caller a follow-up question when the location is ambiguous, checks its own packet, and stops for dispatcher approval before sending. It also runs unattended over the whole dataset to write pre-plans. | C |
| Usefulness | 25% | A named user (dispatcher), a finished output (the packet), and numbers: room accuracy, seconds to packet, meters saved against the front door. A quote from a real public safety officer. | D, C |
| Demo and polish | 15% | One live run from a texted photo to an approved packet in under 60 seconds, with the agent's steps visible and every claim clickable. | D |

### What each layer contributes

| Layer | Question it answers | Derived facts |
|---|---|---|
| CoStar property data | What is this building and who is in that suite? | Use type, stories, year built, size, tenant in the located suite, occupancy risk (clinic, daycare, lab), owner or manager contact |
| Matterport twin | Where is the caller and how do you get there? | Caller's position, room, floor, interior route, stairs and elevators, doors, visible equipment (AED, extinguisher, alarm panel, exit signs) |
| Vexcel aerial | How do you approach from outside? | Entrances on every side, where an engine or ambulance can stage, fire lane and lot layout, roof features, which exterior door is closest to the interior target |

### Joins that only work with more than one layer

Put at least two of these on screen. They are what "use of the data" means to these judges.

1. **Best door.** Aerial finds every entrance. The twin gives interior distance from each entrance to the caller. The answer is often not the door at the street address.
2. **Whose room.** The twin says "second floor, east wing." CoStar says that suite is a dialysis clinic. The packet changes: expect patients who cannot walk out.
3. **Stretcher route.** The shortest walking route uses stairs. CoStar says four stories, the twin shows where the elevator is, and the stretcher route is a different path through a different door.
4. **Record against reality.** CoStar lists the suite as vacant, the twin shows furniture and equipment. The packet flags the mismatch instead of trusting either.

---

## 3. What it does, step by step

**Live mode (one incident)**

1. Caller texts a photo and optionally a sentence. Dispatcher already has the address from the call.
2. Records subagent pulls the CoStar record for that address and writes the building brief.
3. Locator subagent matches the photo against that building's panoramas and returns ranked candidate positions with match evidence.
4. If the top candidates disagree, the agent asks the caller one question chosen to split them ("Is there a window on your left?") or texts back two thumbnails and asks which one looks right.
5. Site subagent reads the aerial tile: entrances, staging spots, obstructions.
6. Route subagent computes routes from each entrance to the caller for two profiles, crew on foot and stretcher, and picks the best door.
7. Verifier subagent checks that every claim in the draft packet has a source and a confidence. Anything unsupported is removed or marked "not confirmed."
8. The agent pauses. The dispatcher approves, edits, or rejects.
9. On approval the packet goes to the crew as a text with a link, and optionally as a 20 second voice brief.

**Batch mode (pre-plans)**

The same pipeline without a caller. For every building in the dataset the agent writes a one-page pre-incident plan and a list of what it could not determine. This is the fallback demo if photo matching fails, and it is a second agentic story: it runs for an hour with no human.

**The responder packet (one page)**

- Location: floor, suite, room, confidence, the matched panorama beside the caller's photo
- Stage here: marked on the aerial tile
- Enter here: the door, with a note if it differs from the street address side
- Route: 4 to 8 steps with panorama thumbnails at each turn, distance, stairs or elevator
- Stretcher route if different
- On the way: locked or narrow doors, stairs, hazards seen in the scan
- Nearby equipment: AED, extinguisher, alarm panel
- Building brief: use type, stories, year built, tenant, contact
- Not confirmed: everything the agent could not see or verify

---

## 4. Deep agent architecture

Framework: LangChain `deepagents` (Python). Pin the version in `requirements.txt` on the first commit.

**Docs check, done today.** The overview page shows `create_deep_agent` taking `model`, `tools`, `system_prompt`, `middleware`, `interrupt_on`, and `memory`. It also says that from v0.7 task planning is opt-in: you only get `write_todos` if you pass `TodoListMiddleware()`. Built-in tools are `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, and `task` for launching subagents. The overview does not show the exact fields of a subagent or the checkpointer parameter, so confirm those in the API reference before writing the wiring. The sketch below is my best understanding, not copied from the docs.

```python
from deepagents import create_deep_agent

commander = create_deep_agent(
    model="anthropic:<model you have keys for>",
    system_prompt=COMMANDER_PROMPT,
    tools=[send_packet, ask_caller, notify_contact],
    middleware=[TodoListMiddleware()],          # planning is opt-in from v0.7
    subagents=[locator, site, router, records, verifier],   # confirm field names
    interrupt_on={"send_packet": True, "notify_contact": True},
    memory=["./AGENTS.md"],                     # confirm expected format
)
```

### Agents

| Agent | Job | Tools | Writes |
|---|---|---|---|
| **Commander** (lead) | Plans the incident, delegates, decides when location is certain enough, assembles the packet, requests approval | `task`, todos, file tools, `ask_caller`, `send_packet`, `notify_contact` | `plan.md`, `packet.md` |
| **Records** | Building brief from CoStar | `get_property`, `get_tenants` | `brief.json` |
| **Locator** | Photo to position | `match_photo`, `look`, `best_question`, `update_belief` | `candidates.json`, `location.json` |
| **Site** | Outside approach from aerial | `get_aerial`, `detect_site_features` | `site.json` |
| **Router** | Fastest routes, best door, backup route, replanning (section 4A) | `best_entry`, `best_entry_under_belief`, `route`, `backup_route`, `replan`, `directions`, `hazards_along`, `find_equipment` | `route.json` |
| **Verifier** | Audits the draft packet | `read_file`, `check_claim` | `audit.json` |

### Rules that keep it fast and correct

- **The model does not do graph math.** Routing, belief updates, and question selection are deterministic tools. The agent decides which to call and what to do with the result.
- **The filesystem is the incident folder.** `/incidents/<id>/` holds every intermediate file. That folder is the trace for the demo and the input for the eval script.
- **Subagents keep the lead's context small.** Panorama images and aerial tiles are only ever seen inside Locator and Site. The Commander reads their JSON.
- **Run Records, Locator and Site in parallel** if the installed version supports parallel `task` calls. Router needs all three.
- **Two approval gates.** `send_packet` and `notify_contact` are in `interrupt_on`. Nothing leaves the system without a human click.
- **Memory with a purpose.** When a dispatcher corrects the packet ("north door is chained after 6 PM"), the agent writes it to `AGENTS.md` under that building. The next incident at that building uses it. Show this in the demo: run the same building twice.

### The part LangChain judges have not seen: active localization

The Locator keeps a belief over candidate positions and treats questions as actions.

- `match_photo` returns the top k candidates with scores. Normalize them into a belief.
- Each panorama position has precomputed attributes: floor, has window, carpet or tile, near elevator, visible signage text, room type.
- `best_question(candidates)` returns the attribute that splits the belief most evenly (highest expected information gain).
- The agent phrases the question, the caller answers, `update_belief` reweights.
- Stop when the top candidate passes a threshold or after 3 questions. Then send two thumbnails and ask which one.

This gives you a score for the agent itself: questions needed to reach the right room, against asking random questions and against asking none.

### Filesystem layout

```
/incidents/<id>/
  intake.json        address, caller photo path, caller text
  plan.md            commander's todo list
  brief.json         CoStar-derived building brief
  candidates.json    ranked positions with scores and evidence
  location.json      final position, room, floor, confidence
  site.json          entrances, staging, obstructions
  route.json         per-entrance routes for both profiles, chosen door
  audit.json         verifier results per claim
  packet.md          the output
  trace.jsonl        every tool call with timing
/buildings/<id>/
  graph.json         nodes, edges, attributes
  preplan.md         batch mode output
AGENTS.md            per-building notes learned from corrections
```

---

## 4A. Routing: fastest way to the caller

Routing is a core feature, not a helper. The packet's two most useful lines, "enter here" and "go this way," both come from it. It is all deterministic code that the Router subagent calls. Owner: A.

### Step 1: get a graph out of the building

Use whichever source the data gives you, in this order.

1. **Matterport positions and connections.** Each panorama position is a node, each connection is an edge. Least work, and every node already has an image.
2. **Positions without connections.** Connect each position to its nearest neighbors on the same floor within a few meters, then delete edges that cross a wall. Check walls with the floor plan if you have one, or by asking the vision model whether node B is visible from node A.
3. **Floor plan image only.** Threshold the plan into walkable and not walkable pixels. Shrink the walkable area by half a body width (or half a stretcher width for that profile) so routes do not hug walls. Then either run A* directly on the pixel grid, or skeletonize the walkable area and use the skeleton's junctions as nodes. Doors are gaps in walls, so mark them as door edges.

Floors connect through stair and elevator nodes: one edge per flight or per elevator ride between the matching nodes on adjacent floors. Exterior nodes (staging spots, each outside door) come from the aerial tile and connect to the interior entrance nodes.

### Step 2: cost is seconds, not meters

Fastest and shortest are different routes once stairs, elevators and doors are involved. Every edge gets a time cost per profile.

| Edge type | On foot | Stretcher |
|---|---|---|
| Open corridor | meters / walking speed | meters / slower speed |
| Door | small fixed delay | larger delay, impassable if too narrow |
| Locked or badge door | large delay, or impassable | same |
| Stairs, per flight | fixed seconds per flight | impassable |
| Elevator | wait time + seconds per floor | wait time + seconds per floor |
| Exterior, vehicle | meters / driving speed in a lot | same |
| Exterior, on foot | meters / walking speed | same |

Put the constants in one config file with sensible starting values and say on the slide that they are tunable assumptions. Ask your interviewee how long a flight of stairs with gear really takes and use their number.

### Step 3: the algorithm

- **One search finds the best door.** Run Dijkstra once, outward from the caller's node, over the whole graph including exterior nodes. That gives the time from every entrance and every staging spot to the caller in a single pass. The best door is the entrance with the lowest total of outside time plus inside time. No need to run one search per door.
- **Run it per profile.** Filter edges by `passable[profile]` first, then search. Two profiles means two searches. Buildings are a few hundred nodes, so each takes milliseconds.
- **A\* for single pairs.** When you only need one start and one goal (replanning mid-route), use A\* with straight-line distance over top speed as the heuristic, plus a floor-difference term. On the pixel grid A\* matters for speed. On the panorama graph plain Dijkstra is enough.
- **Backup route.** Compute a second route that shares as few edges as possible with the first: multiply the cost of every edge on the best route by 3 and search again. Simpler than Yen's k-shortest paths and gives a route that is actually different.

### Step 4: what makes it more than Dijkstra

These are the parts worth showing judges.

1. **Routing under location uncertainty.** If the Locator still has three candidate rooms, do not route to the top one. Compute the time from each entrance to each candidate and pick the door with the lowest expected time under the belief. If the candidates are far apart, the Router tells the Commander that one more question is worth asking. Routing and localization inform each other.
2. **Routing under edge uncertainty.** Perception gives each door and stair a confidence. Use expected cost: time if passable, plus probability blocked times the detour cost. A route through a door the model is 60% sure about loses to a slightly longer route through open corridor.
3. **Live replanning.** The crew texts "door locked" or "stairwell blocked." The agent marks that edge impassable, reruns A\* from the crew's current node, sends the new steps, and writes the correction to `AGENTS.md` so the next incident at that building starts with it.
4. **Two crews.** Fire attack goes by stairs, the stretcher goes by elevator, and they may use different doors. Show both routes on one map.
5. **Way out.** The same search tree gives the fastest exit from the caller's position, for an evacuation instruction to the caller or a patient carry-out.

### Step 5: turn the path into directions

A list of node IDs is useless to a crew. `directions(path)` produces steps:

- Merge consecutive nodes that go straight into one step with a distance.
- At each turn, compute left or right from the change in heading between edges.
- Name the turn using a landmark from node attributes: "left at the elevator lobby," "through the double doors past the reception desk."
- Attach the panorama thumbnail for each turn, facing the direction of travel.
- State floor changes explicitly: "Stair B to floor 3."

The vision model only writes the landmark phrases. Left, right and distance come from geometry.

### Routing tools

```
route(building_id, from_node, to_node, profile) -> {path[], seconds, meters, uses_stairs, uses_elevator}
best_entry(building_id, target, profile) -> {entrance_id, staging_id, seconds, path[], runner_up}
best_entry_under_belief(building_id, candidates, profile) -> {entrance_id, expected_seconds, spread_seconds}
backup_route(building_id, from_node, to_node, profile, avoid_path) -> {path[], seconds}
replan(building_id, current_node, to_node, profile, blocked_edges[]) -> {path[], seconds}
directions(building_id, path) -> [{text, meters, node_id, thumbnail_path}]
exit_route(building_id, from_node, profile) -> {path[], seconds, entrance_id}
```

### Build order for routing

1. Toy graph by hand: 15 nodes, two floors, one stair, one elevator, three entrances, with answers you know. All routing code is tested against this first.
2. `route` with profiles and time costs.
3. `best_entry` using the single reverse search.
4. `directions`.
5. `replan` and `backup_route`.
6. `best_entry_under_belief`.
7. Edge uncertainty, only if B's confidences are trustworthy.

Items 1 to 4 are required. Items 5 and 6 are the demo moments. Item 7 is stretch.

---

## 5. The contract (agree in the first 30 minutes)

One file, `contract.md`. It changes only if all four agree.

**Tool signatures**

```
get_property(address) -> {building_id, use_type, stories, year_built, sqft, tenants[], contact}
match_photo(building_id, image_path, k=10) -> [{node_id, score, evidence_path}]
look(node_id) -> {image_path, attributes, neighbors[]}
best_question(candidates) -> {attribute, question_hint}
update_belief(candidates, attribute, answer) -> candidates
detect_site_features(building_id) -> {entrances[], staging[], obstructions[], tile_path}
entrances(building_id) -> [{entrance_id, node_id, side, latlon}]
(routing tools: see section 4A)
hazards_along(building_id, path) -> [{node_id, type, evidence_path}]
find_equipment(building_id, type, near_node) -> [{node_id, meters, evidence_path}]
ask_caller(incident_id, text, images=[]) -> reply
send_packet(incident_id) -> status        # approval gate
```

**`graph.json`**

- Node: `id`, `floor`, `xyz` if available, `pano_path`, `room_label`, `attributes{}`, `is_entrance`, `equipment[]`
- Edge: `a`, `b`, `meters`, `type` (open, door, locked_door, stairs, elevator, exterior_vehicle, exterior_foot), `seconds{foot, stretcher}`, `passable{foot, stretcher}`, `confidence`, `evidence_path`
- Exterior nodes come from the Site lane and connect to interior entrance nodes.

**Rules**

- Every tool has a stub that returns a fixed valid response within the first hour.
- Every fact carries `evidence_path` or a record ID. No source, no claim.
- Cache every external call to disk.
- If any of the Threshold work exists (graph, `look`, `move`, edge labels), reuse it. The stretcher profile is the wheelchair profile with an elevator requirement.

---

## 6. The four lanes

### Person A: Data and environment

**Mission.** Turn CoStar and Matterport into a graph the others can query, and own routing.

1. Confirm what the Matterport access gives: panorama images, positions, connections between positions, room labels, tags. Report to the team by 2:00 PM.
2. Pick 5 demo buildings that have all three layers, at least two with more than one floor. Mix Houston and LA.
3. `get_property`: address to CoStar record, normalized.
4. Build `graph.json` per building. If connections are not exposed, connect positions by distance on the same floor and mark stairs and elevators by hand for the 5 buildings.
5. `look` and `entrances`.
6. Routing, following the build order in section 4A: toy graph, `route`, `best_entry`, `directions`, then `replan`, `backup_route`, `best_entry_under_belief`.
7. Join exterior nodes from B's site output to interior entrance nodes.
8. Stretch: batch ingest of every building for pre-plan mode.

A has the most work now. Once B's matcher works on clean crops (4:00 PM target), B takes `directions` off A's list, since it needs the node attributes B produces anyway.

**Done when.** 2:00 PM access report. 3:00 PM routing passes on the toy graph. 4:00 PM one real building's graph loads and `best_entry` returns a door and a path. 7:00 PM all 5 buildings with directions. 10:00 PM replanning and routing under belief.

**Fallback.** No programmatic access at all: capture panoramas by hand from the viewer for 3 buildings and build the graph manually. Slow, but everything downstream still works.

### Person B: Perception

**Mission.** Everything that turns pixels into facts.

1. **Photo index.** For each panorama, cut perspective crops (8 headings by 2 pitches), embed with a strong self-supervised vision model, store in FAISS per building.
2. **`match_photo`.** Retrieve top 50 by embedding, re-rank with local feature matching, save the keypoint overlay as `evidence_path`. That overlay is the proof shown on screen.
3. **Node attributes.** One vision model pass per panorama, cached: room type, window, floor surface, signage text, visible equipment, stairs, elevator, hazards. This feeds `best_question`, `find_equipment`, and `hazards_along`.
4. **`detect_site_features`.** Vision model on the aerial tile: entrances by side, staging candidates, fire lane, loading dock, obstructions. Have it return pixel points so D can draw them.
5. **Test queries.** Hold out crops and degrade them the way a phone photo in a bad moment looks: blur, tilt, tight crop, dim light, partial occlusion. Also take real phone photos of the viewer on a laptop screen.
6. Hand-check attributes and site features for the 5 demo buildings with C.

**Done when.** 4:00 PM `match_photo` returns the right room for clean held-out crops on one building. 7:00 PM degraded queries work and attributes exist for all 5. 9:00 PM site features for all 5.

**Fallback.** Embedding match is weak: have the vision model caption every panorama, caption the query photo, and match on text plus the thumbnail confirmation step. Accuracy drops but the active question loop covers the gap, and you report both numbers.

### Person C: Agent and evaluation

**Mission.** Wire the deep agent so an incident runs end to end, and produce the numbers.

1. Install and pin deepagents. Get a trivial agent with one subagent and one interrupt running by 2:00 PM. Confirm subagent fields and checkpointer setup in the API reference.
2. Commander prompt and the five subagents, built against stubs first.
3. `best_question` and `update_belief` (deterministic, about 60 lines).
4. Approval gates with `interrupt_on`, resumable from D's console.
5. Verifier: for each sentence in the packet, find the supporting file and field. Drop or mark what has none.
6. `trace.jsonl` writer with timings.
7. Memory: write dispatcher corrections to `AGENTS.md`, read them on the next run.
8. **Eval script.** One command prints the table in section 9.
9. Batch mode: loop the pipeline with no caller and write `preplan.md` per building.

**Done when.** 2:00 PM trivial agent. 4:00 PM full incident on stubs. 7:00 PM full incident on one real building. 10:00 PM approval, verifier and memory working. Midnight eval table filled.

**Fallback.** Subagents misbehave or runs are slow: collapse to one agent with all tools and keep the file layout. You lose some of the LangChain story but keep the product.

### Person D: Console, channel, and story

**Mission.** Everything a judge sees, hears, or reads.

1. **Dispatcher console** (one page). Left: caller thread and photo. Center: twin view or panorama with the matched position, and the aerial tile with staging and door marked. Right: the agent's live plan and trace, each claim linking to its evidence. Bottom: the packet with Approve, Edit, Reject.
2. **Caller channel.** Photon text in and out, including inbound images. Test in the terminal provider. The free tier caps new conversations per line per day, so save real messages for the demo.
3. **Packet renderer.** `packet.md` to a clean one-page view and a shareable link.
4. **Interview before 5 PM.** Princeton Department of Public Safety, the local fire department or first aid squad, or a building manager. Three questions: what do you know about a building before you arrive, how do you find the room, what do you wish you had. Get a name, a role, a quote, and a photo if they agree. This is the Peri evidence and the first ten seconds of the video.
5. Stretch: voice brief of the packet for the crew (Photon voice prize).
6. README, Devpost, 2 minute video, per-sponsor pitch lines.

**Done when.** 4:00 PM console shell rendering stub files. 5:00 PM interview done. 7:00 PM console showing a real incident. 10:00 PM text channel live and approval buttons wired. Midnight Devpost draft. 6:30 AM video uploaded.

**Fallback.** Photon blocked: a web form that uploads a photo. Keep the text thread look in the console.

---

## 7. Schedule

| Time | What must be true |
|---|---|
| 1:15 to 1:45 PM | Contract written. Stubs committed. 5 buildings chosen (or A reports why not). |
| 2:00 PM | A: data access report. C: trivial deep agent runs. Go or no-go on photo matching as the lead feature. |
| 4:00 PM | Checkpoint 1. Full incident on stubs, visible in the console. One real graph loads. Matcher works on clean crops. |
| 5:00 PM | Interview done. |
| 7:00 PM | Checkpoint 2. Real photo to real location to real packet on one building. First merge to main. |
| 10:00 PM | Checkpoint 3. All 5 buildings. Approval, verifier, memory, text channel working. Second merge. |
| 12:00 AM | Feature freeze except stretch. Eval table filled. Devpost draft exists. |
| 12:00 to 3:00 AM | Stretch items in order: batch pre-plans, voice brief, Beeline beacon. Cut anything shaky. |
| 4:00 AM | Code freeze. Fixes only. |
| 4:00 to 6:30 AM | README with numbers. Record video. Full dry run at 5:00 with everyone. |
| 7:30 AM | Submitted. D submits, A double-checks tracks and Discord usernames. |

**Go or no-go at 2:00 PM.** If panorama images cannot be pulled at all and hand capture is too slow, flip the product: batch pre-plans become the lead feature, and the caller describes the location in text instead of sending a photo. The agent, the packet, the console and the three-layer joins all survive.

---

## 8. Shared jobs

| Job | Who | When |
|---|---|---|
| Contract and stubs | All | 1:15 to 1:45 PM |
| Hand labels for 5 buildings (true room for test photos, real entrances, stairs, elevators) | B and C | 5:00 to 6:30 PM |
| Talk to CoStar, Matterport, Vexcel engineers with a working screen | A and D | As soon as checkpoint 2 passes |
| Talk to LangChain | C | After checkpoint 3 |
| Dry run | All | 5:00 AM |

**Working in parallel with coding agents**

- One folder per lane: `env/`, `perception/`, `agent/`, `app/`. Nobody's agent edits another lane's folder.
- Merge at checkpoints, not continuously.
- Each person connects their coding agent to ChatOverflow.

**Fifth person.** Take evaluation, hand labels, the interview, and the video. That frees C to go deeper on the agent and D to polish the console. Fifth person also owns the Beeline beacon if you attempt hardware.

**If someone is blocked.** A blocked on data access: B moves over, since nothing works without the graph. B's matcher is weak at 7 PM: switch to the caption fallback and stop tuning. C's agent is flaky at 10 PM: collapse to one agent. D behind on the console: cut the twin viewer embed and show static panoramas.

---

## 9. Numbers to report

Print these from one script and put the table in the README, the Devpost, and the video.

| Metric | How | Baseline |
|---|---|---|
| Room accuracy, top 1 and top 5 | Degraded held-out photos across 5 buildings, state the count | Embedding only with no re-rank, and random |
| Median error in meters or graph hops | Distance from predicted to true position | Same |
| Accuracy after questions | Same queries with the question loop on | No questions, and random questions |
| Questions needed | Mean questions to pass the confidence threshold | Random question order |
| Seconds from photo to packet | From `trace.jsonl` | None needed |
| Seconds saved by best door | Route time from chosen door against route time from the street-address door | Front door |
| Fastest against shortest | Share of targets where the time-optimal route differs from the distance-optimal one, and seconds gained | Shortest path by meters |
| Route correctness | Compare against routes hand-traced in the viewer for 10 targets | None needed |
| Replan time | Milliseconds from "door locked" to new directions | None needed |
| Cost of uncertainty | Expected seconds when routing to the belief against routing to the top guess, on queries where top 1 was wrong | Route to top guess |
| Stretcher route differs | Share of test targets where the stretcher route uses a different door or path | None needed |
| Packet claim precision | Hand-check 50 claims across packets | Packet with the verifier turned off |

**A human test if time allows.** Give five hackers a target room in the twin viewer. Half get only the address, half get the packet. Time how long each takes to navigate to the room. That is a usefulness number with real people in it.

Always state the sample size next to the number. Five buildings is a small set and judges will respect you for saying so.

---

## 10. Tracks

| Track | Fit | What to show |
|---|---|---|
| CoStar: Inside and Out | Main target | Section 2 |
| LangChain: best use of deepagents | Strong | Planning, five subagents, filesystem as incident folder, approval gates, memory from corrections, and a score for the agent's question policy |
| Amazon: practical AI | Strong | Real user, finished output, multi-step, adapts to ambiguity |
| Main track: Healthcare | Strong | Time to patient. High on Charity. |
| Photon | Good | Caller texts a photo, agent texts back questions and thumbnails. Voice brief for the voice prize if built. |
| Peri | Good if the interview happens | Named officer or manager, quote, photo |
| Hardware / Physical AI | Stretch only | Beeline beacon on an AED cabinet. Skip unless everything else is frozen by midnight. |
| Capital One, Arlequin | Skip | Do not force them |

---

## 11. Two minute demo

1. **0:00 to 0:15.** The interview quote. "We get an address. Then we go looking."
2. **0:15 to 0:30.** The join. "CoStar knows the building. Matterport knows the room. Vexcel knows the door."
3. **0:30 to 1:15.** Live. A teammate texts a blurry photo. The console shows the plan, the candidates, one question to the caller, the answer, the match with keypoint lines, the aerial with staging and door, the route.
4. **1:15 to 1:30.** Approve. The packet arrives on a second phone.
5. **1:30 to 1:45.** The numbers: accuracy, seconds, meters saved, with sample size.
   If there is room, text "door locked" from the second phone and show the route redraw.
6. **1:45 to 2:00.** Run the same building again after a dispatcher correction to show memory, or show 30 pre-plans written overnight. One sentence on what comes next.

Have a recorded run ready in case the network fails.

---

## 12. Risks and what to say about them

- **Photo matching depends on image access.** Decided at 2:00 PM, with the batch pre-plan fallback above.
- **Never contact real 911, and do not claim to replace it.** The demo uses a mock dispatcher console. Say "this sits beside the dispatcher" out loud.
- **Privacy.** Commercial buildings only, photo is sent by the caller by choice, used for that incident, every query logged. Say it before a judge asks.
- **Scans go stale.** Walls move and tenants change. The packet shows the scan date if available and has a "not confirmed" section. The record-against-reality check turns this weakness into a feature.
- **Wrong location is worse than no location.** Show confidence, show the side-by-side match, and require approval. Below the threshold the packet says "floor and wing only."
- **Small test set.** Report counts with every metric.

---

## 13. Ask sponsors

**CoStar, Matterport, Vexcel**
- Can we pull panorama images, positions and connections programmatically?
- Are room labels or tags available per scan?
- Which property fields are in the hackathon set: tenants by suite, stories, year built, sprinklers, construction type, contacts?
- Is the scan date exposed?
- Aerial resolution and whether oblique views exist, since side entrances are hard to see from straight overhead.

**LangChain**
- Field names for subagent definitions and the recommended checkpointer for interrupts in the current version.
- Do `task` calls run in parallel?
- Best pattern for subagents sharing state through files.

**Photon**
- Can the agent read inbound image bytes?
- What qualifies for the voice prize?

---

## 14. Verify before it goes on a slide

- The CoStar rubric weights and rules, against the packet. I took them from our earlier notes.
- Every CoStar field named in this plan. I have not seen the dataset, so treat field names as placeholders.
- The deepagents sketch in section 4 against the API reference.
- Photon's free tier limits and inbound image support.
- Any statistic about response times or indoor location. Use the interview quote instead of a number you cannot source.

Links
- deepagents overview: https://docs.langchain.com/oss/python/deepagents/overview
- deepagents API reference: https://reference.langchain.com/python/deepagents/
- Photon docs: https://photon.codes/docs/llms-full.txt
