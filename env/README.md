# env: building graphs, CoStar lookup and routing (lane A)

Everything the other lanes call lives in `env/tools.py`. Run commands from the repo root.
Lane B: read `env/HANDOFF_B.md` first. It says what is ready and what is not.

```
pip install -r env/requirements.txt
python3 -m unittest discover -s env/tests -t .     # 44 tests, no network
python3 -m env.tools                                # worked example on the toy building
```

Keys are read from `api.txt` at the repo root (or the env vars `MATTERPORT_KEY`,
`MATTERPORT_TOKEN`, `VEXCEL_API_KEY`).

## Buildings

`building_id` is the CoStar PropertyId as a string. `toy` is a hand-made test building
with known answers.

| building_id | Address | Scan covers | Map placement |
|---|---|---|---|
| 6550930 | 7900 Fannin St, Houston | ground-floor lobby | scan is about 23 m off; doors and staging are placed by hand |
| 10352309 | 12814 Willow Centre Dr, Houston | whole building, 2 floors | good |
| 259587 | 911 Broxton Ave, LA | whole building, 5 levels | good |
| 234036 | 1315 St Joseph Pkwy, Houston | floor 14 | roughly right, within about 10 m (the tower leans in the aerial, so hard to judge) |
| 248351 | 3800 Barham Blvd, LA | ground and 2nd floor of one suite | 20 to 30 m off; do not draw it on a map |
| 248192 | 3250 Wilshire Blvd, LA | Suite 301 and its corridor, floor 3 | looks off: plots over the street beside the tower |

Per building, under `buildings/<id>/`:

- `graph.json`: nodes and edges (schema in `env/graph.py`). Rebuilt by the builder; do not edit by hand.
- `overrides.json`: hand edits (entrances, elevators, staging, locked doors). See `env/MARKING.md`.
- `attributes.json`: lane B's per-node facts (see below). Optional.
- `evidence/<node id>.jpg`: a crop showing each marked door and elevator.
- `panos/<sweep id>.jpg`: one panorama per scanned position. Not in git: run
  `python3 -m env.views panos <id>` (about 2 s each).
- `graph_floor<n>.jpg`, `aerial.jpg`: the graph over the floor plan, and over the aerial tile.

Rebuild after changing `overrides.json` or `attributes.json`:

```
python3 -m env.build_graph --building <id> --render      # or --all
```

## Tools (`from env import tools`)

| Tool | Returns |
|---|---|
| `get_property(address)` | `{building_id, use_type, stories, year_built, sqft, tenants[], contact, occupancy_risks[], ...}`; also accepts a PropertyId or a Matterport model id |
| `get_tenants(building_id, floor=None, suite=None)` | current tenants with `risk_flags` |
| `list_buildings()` | buildings that have a graph |
| `look(node_id)` | `{image_path, attributes, room_label, equipment, neighbors[] (ids), neighbor_details[]}` |
| `entrances(building_id)` | `[{entrance_id, node_id, side, latlon, label, primary, confirmed, evidence_path}]` |
| `route(building_id, from_node, to_node, profile)` | `{path[], seconds, meters, uses_stairs, uses_elevator, reachable}` |
| `best_entry(building_id, target, profile)` | `{entrance_id, staging_id, seconds, path[], runner_up, ranked[], front_door, carry}` |
| `best_entry_under_belief(building_id, candidates, profile)` | `{entrance_id, visit_order[], expected_seconds, spread_seconds, value_of_information_seconds, ask_another_question, top_guess}` |
| `backup_route(building_id, from_node, to_node, profile, avoid_path=None)` | `{path[], seconds, shared_edges[], is_different}` |
| `replan(building_id, current_node, to_node, profile, blocked_edges)` | `{path[], seconds, compute_ms, reachable}` |
| `directions(building_id, path)` | `[{text, meters, node_id, thumbnail_path, heading_deg, floor, kind}]` |
| `exit_route(building_id, from_node, profile)` | `{path[], seconds, entrance_id, ranked[], nearest_stair_or_outside}` |
| `hazards_along(building_id, path)` | `[{node_id, type, evidence_path}]` |
| `find_equipment(building_id, type, near_node)` | `[{node_id, meters, seconds, evidence_path}]` |
| `report_edge(building_id, a, b, status, note)` | records "blocked", "locked" or "clear" for the next incident |

`profile` is `"foot"` or `"stretcher"`. A bad argument comes back as `{"error": "..."}`.
Time constants are assumptions in `env/config.py` (`ROUTING`).

Things an agent should read in the results:

- `best_entry(...)["scope"]` says what the scan does not cover. For a scan that starts on
  an upper floor, `outside_seconds` is the unscanned approach from the street and
  `scanned_seconds` is the part we can show.
- When a stretcher cannot reach the target, `reachable` is false and `carry` says where
  to leave it and how long the carry is.
- `confirmed: false` on an entrance, and `unconfirmed_*` from `hazards_along`, mean
  nobody has seen that the door leads where we think it does.

## For lane B (perception)

- **Images.** `panos/<sweep id>.jpg` is an equirectangular panorama in world axes: the
  center column looks along +x, and every panorama of a building shares those axes. For
  undistorted crops use
  `env.views.view_heading(model_id, sweep_id, heading_deg, hfov_deg=90, pitch_deg=0, size=768, resolution="high")`,
  which samples the cube faces directly. The model id is `graph["matterport_model_id"]`.
- **Node ids.** The graphs use the sweep id alone. The tools also accept lane B's
  `"<model id>/<sweep id>"` form and answer with the graph's form. Doors, elevators and
  staging spots are nodes too but have no image.
- **Per-node facts** go in `buildings/<id>/attributes.json`, then rebuild.
  `python3 -m env.import_attributes <id>` writes that file from
  `cache/attributes/<id>/_all.json`. The format, if you write it directly:

  ```json
  {"<node id>": {"room_label": "Exam room", "label": "the nurses' station",
                 "attributes": {"window": true, "floor_surface": "carpet",
                                "signage": ["Suite 1425"], "hazards": ["oxygen cylinders"]},
                 "equipment": [{"type": "aed", "evidence_path": "perception/evidence/x.jpg"}]}}
  ```

  `label` is a specific spot and appears in directions as "turn left at ...".
  `room_label` appears as "into the ...". `attributes.hazards` feeds `hazards_along`,
  `equipment` feeds `find_equipment`. Hand edits in `overrides.json` win over this file.
- **Site output.** Staging spots are currently placed by eye. When `detect_site_features`
  exists, give lane A staging spots as lat/lon and they go into `overrides.json` as
  `add_nodes` with `kind: "staging"`. Aerial detection does not find doors, so entrances
  come from the scan. Check the "Map placement" column above before trusting any lat/lon
  in `graph.json`.
- **What the scans are.** Mostly space offered for lease, so the scanned suite itself
  usually has no tenant on record. Tenant facts are reliable for the other floors.

## Numbers

`python3 -m env.metrics` prints the routing rows for the results table and writes
`buildings/routing_metrics.json`.
