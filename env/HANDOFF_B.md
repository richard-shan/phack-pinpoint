# Handoff from lane A to lane B (perception)

Written Saturday 2:40 PM by lane A. Read `env/README.md` for the tool table and file
layout; this note says what is ready, what is not, and what lane A needs back.

## Good to go

- **Every tool in `env/tools.py`.** 43 tests pass with no network
  (`python3 -m unittest discover -s env/tests -t .`).
- **Routing in all six buildings.** 693 scanned positions, every one reachable on foot.
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

Start with **10352309** and **259587**: whole-building scans, wall plans on every floor,
correct map placement. **6550930** is the strongest story (17 health care tenants, and
the best door is not the street-side door) but its scan has no wall plan and no rooms.

## Not good to go: use with care

1. **Map placement is reliable at two of six buildings.** The scan's own georeference
   puts Willow Centre and Broxton on the right roofs. Fannin, Barham and Wilshire are
   off by 20 m or more. Do not use `latlon` on scanned positions in those three to crop
   aerial imagery or to draw a caller on a map. `buildings/<id>/aerial.jpg` shows where
   each scan lands.
2. **Staging spots are placed by eye** from the aerial tile and marked
   `confirmed: false`. They exist only at Willow Centre, Broxton and Fannin. Your site
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
