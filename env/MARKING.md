# Marking a building by hand

The graph builder gets walls, doorways and stairs from the scan. It cannot see elevators,
glass storefront doors, which doors lead outside on an upper floor, or where a vehicle
can stop. Those go in `buildings/<building_id>/overrides.json`, which the builder applies
on every rebuild. Run everything from the repo root.

## What to mark

1. **Entrances.** Every way a crew gets into the scanned area: exterior doors at street
   level, or for an upper-floor suite its doors to the common corridor. The builder
   proposes candidates (nodes named `door_...`, `confirmed: false`). Check each one.
2. **Elevators.** Doors with call buttons. In a multi-floor scan use `elevators` with one
   lobby sweep per stop. In a single-floor scan add one node with `kind: "elevator"`.
3. **Stair doors** that lead to a stairwell the scan does not enter.
4. **The front door**: `primary: true` on the entrance at the street address.
5. **Approach**, for scans that do not start at street level (see below).
6. **Staging spots**, only for scans that include street-level exterior doors.
7. Worth recording when you see it: suite numbers or tenant names on signs, AEDs, fire
   extinguishers, alarm panels, signs such as "this door must remain locked".

## Looking at the scan

```
python3 -m env.render <id> --labels            # floor plan with the first 5 characters of each node id
python3 -m env.views sheet <id> --floor 0 --near <node prefix or x,y> --radius 8 --max 6 --out sheet.jpg
python3 -m env.views sheet <id> --nodes abcde,fghij --res high --out sheet.jpg
python3 -m env.views view <id> <node prefix> --heading 103 --fov 80 --res high --out view.jpg
python3 -m env.views ray <id> <node prefix> 103     # first wall along that heading, and nodes ahead
```

A sheet shows panoramas in world axes. The ruler under each one gives the heading
(degrees counterclockwise from +x) of each column, so "the door is under the 90 tick"
means heading 90 from that sweep. `view` confirms it with an undistorted crop. `ray`
gives the distance to the wall there when the scan has a wall plan; without one, use the
nodes it lists ahead, or floor tiles, to estimate distance and say it is an estimate.

Node ids are long. Commands accept a unique prefix, but `overrides.json` needs the full
id: copy it from `graph.json` or from the command output. Never type an id from memory.

## overrides.json

```json
{
 "name": "1315 St Joseph Pkwy, 14th floor",
 "floor_offset": 13,
 "scope": "14th floor only. The lobby, elevators below and other floors are not scanned.",
 "nodes": {
  "door_ab12cd34_0": {"confirmed": true, "label": "the Suite 1400 main door", "primary": true,
                      "approach": {"via": "elevator", "floors": 13, "note": "corridor length unknown"}}
 },
 "remove_nodes": ["door_ef56gh78_0"],
 "add_nodes": [
  {"id": "elev_lobby", "near": "<full sweep id>", "heading_deg": 180, "distance_m": 2.5,
   "kind": "elevator", "label": "the passenger elevators", "is_entrance": true,
   "approach": {"via": "elevator", "floors": 13}},
  {"id": "stair_a", "near": "<full sweep id>", "heading_deg": 270, "distance_m": 3.0,
   "edge_type": "door", "is_entrance": true, "label": "the Stair A door",
   "approach": {"via": "stairs", "floors": 13}},
  {"id": "stage_front", "kind": "staging", "latlon": [29.69151, -95.40345], "label": "the front drive"}
 ],
 "add_edges": [{"a": "stage_front", "b": "door_front", "type": "exterior_foot"}],
 "edges": [{"a": "<sweep id>", "b": "door_ab12cd34_0", "type": "locked_door", "note": "sign: keep locked"}],
 "elevators": [{"id": "elev", "label": "the elevator", "offset_xy": [2.2, 0.0],
                "stops": {"1": "<lobby sweep on floor index 1>", "2": "<lobby sweep on floor index 2>"}}]
}
```

- `add_nodes` with `near`, `heading_deg`, `distance_m` places the node that far from the
  sweep and links the two. `edge_type` is the link: `door` for a door, `open` (default)
  for an elevator lobby. The sweep becomes `seen_from`, which the evidence crop uses.
- `floor_offset`: scan floor index 0 is building floor `1 + floor_offset`.
- `approach` on an entrance is the unscanned leg from the street: `via` `elevator` or
  `stairs`, `floors` to climb (building floor minus one), optional `walk_m`. A stretcher
  cannot use a stairs approach. Leave it off for street-level doors.
- `approach: {"locked_entry": true}` (alone, or with `via`) means the door is expected to
  be locked on the way in but opens freely on the way out: a stair door signed NO
  RE-ENTRY, an exit door with a panic bar, a door bolted from inside. Use this instead of
  a `locked_door` edge, which would also slow down the way out.
- `remove_nodes` drops a candidate that turned out not to be a way in (a closet, a
  restroom). A door to a balcony or roof is not an entrance either.
- Edge types: `open`, `door`, `locked_door`, `stairs`, `elevator`, `exterior_foot`,
  `exterior_vehicle`. To change a link that already exists use `edges`, with the same
  two node ids.
- Staging spots: run `python3 -m env.aerial <id> --half 70`, open `buildings/<id>/aerial.jpg`,
  pick a pixel where a vehicle can stop, and convert it with
  `python3 -c "from env.aerial import pixel_to_latlon; print(pixel_to_latlon('<id>', x, y))"`.
  Then link each staging spot to the entrances it serves with `exterior_foot` edges.

## Rebuild and check

```
python3 -m env.build_graph --model <model id> --building <id> --render
python3 -m env.views evidence <id>          # one crop per marked door and elevator
python3 -m env.build_graph --model <model id> --building <id> --render   # picks the crops up
```

Open every crop in `buildings/<id>/evidence/`. If a crop does not show what the node
claims, fix the heading or remove the node, delete that crop, and rebuild. Then route to
a few positions for both profiles:

```
python3 -c "
from env import tools
for p in ('foot', 'stretcher'):
    r = tools.best_entry('<id>', '<target node id>', p)
    print(p, r.get('label'), r.get('seconds'), r.get('reason'))
    for s in tools.directions('<id>', r.get('path') or []): print('   ', s['text'])"
```

## Rules

- Mark only what you can see. If you are inferring (a panic bar suggests exit-only), say
  so in a `note` and lower `confidence`.
- Every marked door and elevator must end up with an evidence crop that shows it.
- Edit only `buildings/<id>/overrides.json`. Do not edit `env/`.
