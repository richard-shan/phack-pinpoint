# Handoff from lane A to lanes B, C and D

Written Saturday 2:40 PM by lane A; the lane D section was added Saturday 5:45 PM. Read `env/README.md` for the tool table and file
layout; this note says what is ready, what is not, and what lane A needs back.

## Good to go

- **Every tool in `env/tools.py`.** 52 tests pass with no network
  (`python3 -m unittest discover -s env/tests -t .`).
- **Routing in all seven buildings.** 716 scanned positions, every one reachable on foot.
  Each marked door and elevator has an evidence crop under `buildings/<id>/evidence/`.
- **`get_property` and `get_tenants`** for all 3,339 CoStar properties.
- **Images for matching.** `python3 -m env.views panos <id>` renders one panorama per
  scanned position into `buildings/<id>/panos/` (about 2 s each, not in git, needs the
  keys in `api.txt`). For crops, `env.views.view_heading(...)` samples the cube faces
  directly and is sharper than cutting the panorama.

| building_id | Address | Scanned positions | Photo matching | Routing | Lat/lon for aerial work |
|---|---|---|---|---|---|
| 10352309 | 12814 Willow Centre Dr, Houston | 114, 2 floors | good to go | good to go | good to go |
| 259587 | 911 Broxton Ave, LA | 190, 5 levels | good to go | good to go | good to go |
| 6550930 | 7900 Fannin St, Houston | 35, lobby only | good to go | good to go | doors and staging only (placed by hand); scanned positions are about 23 m off |
| 234036 | 1315 St Joseph Pkwy, Houston | 145, floor 14 | good to go | good to go | roughly right, within about 10 m |
| 248192 | 3250 Wilshire Blvd, LA | 124, floor 3 | good to go | good to go | not usable: plots over the street |
| 248351 | 3800 Barham Blvd, LA | 85, 2 floors | good to go | good to go, one door unverified | not usable: 20 to 30 m off |
| 5956257 | 3160 Geneva St, LA | 23, ground floor of one wing | good to go, but the floor is bare concrete and looks alike end to end | good to go | doors and staging only (placed by hand); scanned positions are about 25 m off |

Start with **10352309** and **259587**: whole-building scans, wall plans on every floor,
correct map placement. **6550930** is the strongest story (17 health care tenants, and
the best door is not the street-side door) but its scan has no wall plan and no rooms.

## Not good to go: use with care

1. **Map placement is reliable at two of seven buildings.** The scan's own georeference
   puts Willow Centre and Broxton on the right roofs. Fannin, Barham, Wilshire and Geneva
   St are off by 20 m or more. Do not use `latlon` on scanned positions in those four to crop
   aerial imagery or to draw a caller on a map. `buildings/<id>/aerial.jpg` shows where
   each scan lands.
2. **Staging spots are placed by eye** from the aerial tile and marked
   `confirmed: false`. They exist only at Willow Centre, Broxton, Fannin and Geneva St. Your site
   analysis should replace them.
3. **Richard's earlier lane-B code is not in this repo.** It is on his machine at
   `old/phack-falco/perception/`: a cached Vexcel client (`vexcel.py`), an aerial site
   analysis (`site.py`) and a cached vision-labeling helper (`label.py`). Ask him for it
   before writing those again. Its notes record that Vexcel's `elements/detect` found
   cars and painted markings but returned nothing for "door", "building entrance" or
   "curb ramp", on top-down and oblique imagery alike. `env/aerial.py` has a minimal
   top-down tile fetcher if you only need an image.
4. **Room names are mostly missing.** Matterport names rooms at Willow Centre (114 of
   114 positions), Barham (72 of 85) and Wilshire (51 of 124), and none at Broxton,
   Fannin or St Joseph. Directions there say "turn left, go 9 m" with no landmark until
   your attribute pass fills them in.
5. **Two doors are unverified**: the side exit door at Broxton (assumed locked from
   outside because of its panic bar) and the 2nd-floor corridor door at Barham (where it
   leads is inferred from a posted evacuation plan). A stretcher reaches Barham's upper
   level only through that door.
6. **The scans are of space offered for lease**, so the scanned suite or floor usually
   has no tenant on record (nobody is listed on floor 14 at St Joseph). Tenant facts are
   reliable for the other floors of the same building.
7. **All time constants are placeholders** in `env/config.py` (`ROUTING`).

## How this lines up with the `perception` branch (checked Saturday 2:55 PM)

Lane A read `origin/perception` at commit 9f96966. Both lanes use the CoStar PropertyId as
`building_id`. The differences, and what lane A did about them:

- **Node ids.** Perception writes `"<model id>/<sweep id>"`; the graphs use the sweep id
  alone. Every tool in `env/tools.py` now accepts either form and answers with the
  graph's form. To get back to yours, prefix `graph["matterport_model_id"]`.
- **One scan per graph.** Perception treats a building as every scan CoStar lists for the
  property. Each graph here is built from one scan (listed in `env/README.md`). A match
  in another scan of the same property has no route yet: 1315 St Joseph has three more
  suite scans that are not in its graph.
- **Attributes.** `python3 -m env.import_attributes <building_id>` converts your
  `cache/attributes/<building_id>/_all.json` into `buildings/<building_id>/attributes.json`
  (`landmark` becomes the node `label`, `room_type` the `room_label`, equipment and
  hazards keep their evidence paths). Then rebuild the graph.
- **Equipment names.** Your vocabulary (`fire_extinguisher`, `fire_alarm_pull_station`,
  `alarm_panel`, `aed`, ...) is the one the graphs use now.
- **`find_equipment` and `hazards_along`** exist in both lanes. Lane A's measure travel
  time through the graph; yours measure straight-line distance. Once attributes are
  imported, lane A's return the same items with `meters` and `seconds` along the route.
- **Your example building 240517** (17270 Red Oak Dr, four scans) has no graph yet. Say
  which scan matters and lane A will build it; it takes a few seconds plus marking.
- **Site output is not joined yet.** `detect_site_features` returns staging spots and
  entrance candidates with lat/lon. Lane A still places staging by eye and has not
  written the step that turns your output into staging nodes.

## For lane C: swapping `agent/pinpoint/routing.py` for `env/tools.py`

Lane A ran `origin/connor/work`'s routing module and its `building_bayou` fixture graph
side by side with `env/routing.py`, with the same time constants on both sides:
`route()` agrees on all 420 node pairs for both profiles. What changes on the swap:

- **`best_entry` ranks by outside plus inside time**, as the plan says; the stub ranks by
  inside time only. The door changed for 12 of the fixture's 30 targets. `inside_seconds`
  and `outside_seconds` are both returned.
- **`best_entry(...)["path"]` starts at the staging spot** when the building has one, not
  at the door. `entrance_id` is the door.
- **The front door is not `ext_main`.** Use `best_entry(...)["front_door"]`
  (`{entrance_id, seconds, seconds_saved, is_best}`) or `tools.front_door(building_id)`.
- **`question_worth_asking`** here means "knowing the room would save more than 15 s", not
  "the candidates disagree on a door". `all_candidates_same_door` is also returned if you
  prefer your rule. Candidates may carry `belief`, `p` or `score`; `belief` wins.
- **`floor` on a node is the scan's floor index from 0.** Show `floor_name` instead
  ("Floor 14", "Basement 1").
- **Two sets of time constants exist**: `agent/pinpoint/config.py` and `env/config.py`
  (`ROUTING`). They differ (locked door 30 s there, 90 s here). Pick one.
- `tools.load_graph(building_id)` returns the `{nodes, adj, edges}` shape your code reads.

## Update, Saturday evening: everything is on `main` and wired together

All four lanes are merged into `main`, and `live/` runs one incident through all of them
on real data (root `README.md` has the commands). What changed relative to the notes
below:

- The console now opens its 3D route view for any building with
  `app/data/<PropertyId>-scan.json`. `python3 -m live.setup` writes that file for every
  lane A building, so nothing has to be copied by hand.
- The agent's stub tools are replaced at run time by `live/tools.py`, which calls
  `env/tools.py` and `perception.tools.match_photo`. No file in `agent/` was edited.
- Node ids from the matcher (`"<model id>/<sweep id>"`) are mapped to graph ids in
  `live/tools.py`. Matches in a scan the graph does not cover are dropped.

The sections below are the notes each lane was given before the merge.

## For lane D: the console (checked Saturday 5:45 PM)

Lane A read pull request 1 (`codex/lane-d-dispatch-console`, commit 476342d), ran its 35
tests (all pass) and ran its route planner (`app/src/live-route-model.js`) against lane
A's graphs.

- **Nothing collides.** The pull request touches only `app/`; it merges with `lane-a`
  without conflicts.
- **The console does not call lane A yet.** Its two 3D demos route over chains of scan
  positions written by hand in `app/scripts/setup-*.mjs`, and its planner picks the
  shortest distance from an entry the dispatcher chooses.
- **Same ids, same coordinates.** For 3160 Geneva St the console's 23 scan positions are
  the same 23 sweep ids as lane A's graph, and the positions agree to 1 mm. Both use the
  CoStar PropertyId and the profile names `foot` and `stretcher`.
- **Lane A's graph in the console's format** is `buildings/<id>/console_scan.json`, for
  all seven buildings. `tools.console_route(...)` gives a route as the console's request
  body. The console's planner accepted all 1,360 lane A routes: every scanned position
  in the seven buildings for both profiles, less the 72 a stretcher cannot reach.
- **Geneva St needs no change in `app/`:** copy `buildings/5956257/console_scan.json` to
  `app/data/los-angeles-scan.json` and delete the saved Los Angeles incident under
  `app/.local-state/`. The demo caller (scan 10) stays where it is.
- **What the scan shows at Geneva St that the hand-drawn version misses.** The console's
  entry is scan 0 with an approach from the south. Scan 0 is the lobby position beside
  the elevator, which is taped off; there is no door there, and the aerial approach from
  the south runs through the building. The doors are on the Geneva St (north) side next
  to scan 2, and at the east end. Signs on the Geneva St doors send visitors and
  deliveries to 4th Street. Evidence crops are in `buildings/5956257/evidence/`.
- **Any other building needs two small changes in `app/`,** which are lane D's to make:
  `model()` in `app/lib/spatial-service.mjs` maps only two property ids to a file, and
  `app/src/live.js` opens the 3D view only for those two ids. Reading
  `data/<propertyId>-scan.json` when it exists would cover every lane A building.
- **Princeton (Rabinowitz Hall).** The scan is visible to the hackathon API key and lane
  A's builder turns it into one connected graph (66 positions on 2 levels, stairs found
  from the scan). It is not saved, because the two places where the scan starts still
  need marking as entries. Say the word and lane A will do it. It has no CoStar record.
- **Aerial view.** `entries[].approach` runs from the staging spot to the door. It is
  placed by hand at Willow Centre, Broxton, Fannin and Geneva St. At Barham, Wilshire and
  St Joseph it comes from the scan's georeference and is off; `calibration` says so.
- **Not done:** the console still recomputes with its own planner when the dispatcher
  changes profile or blocks a segment. Lane A's answer for that is
  `tools.console_route(..., blocked_edges=[[a, b]])`, which nothing calls yet.

**The lanes are on different buildings.** Perception's example is 240517 (17270 Red Oak
Dr), the agent's fixture is a made-up building, the console demos 5956257 and Princeton
and opens on 350372 (6550 Mapleridge St). Lane A has graphs for seven, including 5956257
but not 240517 or 350372. The team needs to pick the demo building.

## What lane A needs back

- **`buildings/<id>/attributes.json`**: your per-node facts, in the format shown in
  `env/README.md`. The most useful fields for routing are `label` (a specific landmark:
  directions say "turn left at ..."), `room_label`, `attributes.hazards`, and `equipment`
  with an `evidence_path`. Then `python3 -m env.build_graph --building <id>`.
- **`match_photo` node ids** must be sweep ids from `graph.json` (`kind == "pano"`).
- **Staging spots as lat/lon**, with a note on which side of the building each serves.
- **Anything you see that contradicts the graph**: a door marked open that is blocked, an
  elevator we missed, a sign such as "keep locked". `env/MARKING.md` explains how those go
  into `overrides.json`, or tell lane A.

The plan has B taking `directions` over from A once the matcher works. `directions` is
already built in `env/routing.py`; what it still needs from B is the landmark labels above.
