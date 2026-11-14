"""Images out of the scan: a view from a sweep toward a point, and a full panorama.

    python -m env.views evidence <building_id>   # a crop of every door and elevator we claim
    python -m env.views panos <building_id>      # the panorama image behind every pano_path
    python -m env.views sheet <building_id> --floor 0 --near <node> --out sheet.jpg
    python -m env.views view <building_id> <node> --heading 103 --out view.jpg
    python -m env.views ray <building_id> <node> 103

Cube faces are downloaded once and cached under env/cache/matterport/.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from . import config, crop
from . import matterport as mp


def _fetcher(model_id: str, sweep_id: str, resolution: str):
    faces: dict[int, Image.Image] = {}

    def get(i: int) -> Image.Image:
        if i not in faces:
            faces[i] = mp.get_face(model_id, sweep_id, i, resolution)
        return faces[i]
    return get


def view_toward(model_id: str, sweep_id: str, target_xyz, *, hfov_deg: float = 90,
                pitch_deg: float = 0, size: int = 768, resolution: str = "high") -> Image.Image:
    """Perspective view from a sweep's camera toward a point in model coordinates."""
    s = mp.get_sweeps(model_id)[sweep_id]
    direction = np.subtract(target_xyz, s["pano_xyz"])
    return crop.render_view(_fetcher(model_id, sweep_id, resolution), s["quat"], direction,
                            hfov_deg, pitch_deg, size, supersample=1)


def view_heading(model_id: str, sweep_id: str, heading_deg: float, **kw) -> Image.Image:
    """Level view from a sweep along a heading (degrees counterclockwise from +x)."""
    s = mp.get_sweeps(model_id)[sweep_id]
    h = math.radians(heading_deg)
    target = np.add(s["pano_xyz"], [math.cos(h), math.sin(h), 0.0])
    return view_toward(model_id, sweep_id, target, **kw)       # kw: hfov_deg, pitch_deg, size, resolution


def panorama(model_id: str, sweep_id: str, width: int = 2048, resolution: str = "high") -> Image.Image:
    """Equirectangular panorama in world axes: the left edge looks along -x, the center
    along +x, and +y (counterclockwise) is to the left of center. Every panorama of a
    scan shares these axes, so the same column means the same direction in each."""
    s = mp.get_sweeps(model_id)[sweep_id]
    h = width // 2
    yaw = math.pi - (np.arange(width) + 0.5) / width * 2 * math.pi       # +pi .. -pi
    pitch = math.pi / 2 - (np.arange(h) + 0.5) / h * math.pi             # up .. down
    cy, cp = np.cos(pitch)[:, None], np.sin(pitch)[:, None]
    rays = np.stack([cy * np.cos(yaw)[None, :], cy * np.sin(yaw)[None, :],
                     np.broadcast_to(cp, (h, width))], axis=-1)
    local = rays @ crop.world_to_local(s["quat"]).T
    return Image.fromarray(crop.sample_cube(_fetcher(model_id, sweep_id, resolution), local))


def pano_file(graph: dict, node: dict, resolution: str = "high") -> Path | None:
    """Render (once) the panorama a node's `pano_path` points at."""
    if not node.get("pano_path") or node.get("kind", "pano") != "pano":
        return None
    path = config.ROOT / node["pano_path"]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        panorama(graph["matterport_model_id"], node["id"], resolution=resolution).save(path, quality=88)
    return path


def evidence(building_id: str) -> list[Path]:
    """A crop for every door or elevator that names the sweep it was seen from, looking at
    it from that sweep. Saved as buildings/<id>/evidence/<node id>.jpg; the builder picks
    these files up as each node's `evidence_path`."""
    gpath = config.BUILDINGS / str(building_id) / "graph.json"
    g = json.loads(gpath.read_text())
    out = []
    for n in g["nodes"]:
        seen_from = (n.get("attributes") or {}).get("seen_from")
        if not seen_from or not n.get("xyz"):
            continue
        path = gpath.parent / "evidence" / f"{n['id']}.jpg"
        if not path.exists():
            target = [n["xyz"][0], n["xyz"][1], n["xyz"][2] + 1.2]
            img = view_toward(g["matterport_model_id"], seen_from, target, hfov_deg=80, size=640)
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path, quality=88)
        n["evidence_path"] = str(path.relative_to(config.ROOT))
        out.append(path)
    gpath.write_text(json.dumps(g, indent=1))
    return out


def resolve(g: dict, prefix: str) -> dict:
    """The node whose id starts with `prefix` (ids are long; a unique prefix is enough)."""
    hits = [n for n in g["nodes"] if n["id"].startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(f"node prefix {prefix!r} matches {len(hits)} nodes")
    return hits[0]


def _load(building_id: str) -> dict:
    return json.loads((config.BUILDINGS / str(building_id) / "graph.json").read_text())


RULER = (180, 135, 90, 45, 0, 315, 270, 225)


def contact_sheet(building_id: str, out: str, *, floor: int | None = None, near: str | None = None,
                  radius: float = 8.0, nodes: list[str] | None = None, resolution: str = "low",
                  limit: int = 8) -> list[str]:
    """Labeled panoramas, two per row, to look for doors, elevators and signs.

    Each panorama is in world axes with a heading ruler: the number under a tick is the
    heading (degrees counterclockwise from +x) you are looking along at that column. Pick
    sweeps by `nodes` (id prefixes), or by `floor` and distance from `near` (a node prefix
    or "x,y"). Returns the full ids shown, nearest first.
    """
    from PIL import ImageDraw
    g = _load(building_id)
    panos = [n for n in g["nodes"] if n.get("kind", "pano") == "pano"]
    if nodes:
        pick = [(0.0, resolve(g, p)) for p in nodes]
    else:
        if near and "," in near:
            cx, cy = (float(v) for v in near.split(","))
        elif near:
            cx, cy = resolve(g, near)["xyz"][:2]
            floor = resolve(g, near)["floor"] if floor is None else floor
        else:
            cx = cy = None
        pick = []
        for n in panos:
            if floor is not None and n["floor"] != floor:
                continue
            d = math.dist(n["xyz"][:2], (cx, cy)) if cx is not None else 0.0
            if cx is None or d <= radius:
                pick.append((d, n))
        pick.sort(key=lambda t: (t[0], t[1]["id"]))
    pick = pick[:limit]
    if not pick:
        raise SystemExit("no sweeps match")
    W, H = 1024, 512
    sheet = Image.new("RGB", (W * min(2, len(pick)), (H + 18) * ((len(pick) + 1) // 2)))
    d = ImageDraw.Draw(sheet)
    for i, (dist, n) in enumerate(pick):
        x0, y0 = (i % 2) * W, (i // 2) * (H + 18)
        sheet.paste(panorama(g["matterport_model_id"], n["id"], width=W, resolution=resolution), (x0, y0))
        d.rectangle([x0, y0, x0 + 430, y0 + 15], fill=(0, 0, 0))
        d.text((x0 + 4, y0 + 2), f"{n['id'][:12]}  floor {n['floor']}  xy=({n['xyz'][0]:.1f},{n['xyz'][1]:.1f})"
                                 f"  {dist:.1f} m", fill=(255, 255, 0))
        d.rectangle([x0, y0 + H, x0 + W, y0 + H + 18], fill=(0, 0, 0))
        for deg in RULER:
            col = x0 + ((180 - deg) % 360) / 360 * W
            d.line([col, y0 + H - 14, col, y0 + H], fill=(255, 255, 0), width=2)
            d.text((col + 3, y0 + H + 3), str(deg), fill=(255, 255, 0))
    sheet.save(out, quality=82)
    return [n["id"] for _, n in pick]


def ray(building_id: str, node_prefix: str, heading_deg: float, reach: float = 40.0) -> dict:
    """What lies along a heading from a sweep: the first wall of the scan's wall plan (and
    whether a doorway is there), and the sweeps roughly in that direction with distances."""
    from .build_graph import seg_hit
    g = _load(building_id)
    n = resolve(g, node_prefix)
    p = n["xyz"][:2]
    h = math.radians(heading_deg)
    q = (p[0] + reach * math.cos(h), p[1] + reach * math.sin(h))
    best = None
    for w in mp.get_walls(g["matterport_model_id"]).get(n["floor"]) or []:
        if w["type"] != "wall":
            continue
        hit = seg_hit(p, q, w["a"], w["b"])
        if hit and (best is None or hit[0] < best[0]):
            best = (hit[0], w, hit[1])
    wall = None
    if best:
        t, w, u = best
        along = u * w["length"]
        through = next((o for o in w["openings"] if abs(along - o["center_m"]) <= o["width"] / 2 + 0.2), None)
        wall = {"distance_m": round(t * reach, 2), "wall_id": w["id"],
                "opening_here": through and {"type": through["type"], "width": round(through["width"], 2)},
                "openings_on_wall": [{"type": o["type"], "width": round(o["width"], 2),
                                      "meters_from_hit": round(o["center_m"] - along, 2)} for o in w["openings"]]}
    ahead = []
    for m in g["nodes"]:
        if m["id"] == n["id"] or m["floor"] != n["floor"] or not m.get("xyz"):
            continue
        dx, dy = m["xyz"][0] - p[0], m["xyz"][1] - p[1]
        dist = math.hypot(dx, dy)
        off = (math.degrees(math.atan2(dy, dx)) - heading_deg + 180) % 360 - 180
        if dist <= reach and abs(off) <= 15:
            ahead.append({"node_id": m["id"], "kind": m.get("kind", "pano"), "distance_m": round(dist, 2),
                          "off_heading_deg": round(off, 1), "label": m.get("label")})
    return {"from": n["id"], "heading_deg": heading_deg, "first_wall": wall,
            "has_wall_plan": bool(mp.get_walls(g["matterport_model_id"]).get(n["floor"])),
            "nodes_ahead": sorted(ahead, key=lambda a: a["distance_m"])[:8]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="what", required=True)
    for name in ("evidence", "panos"):
        sub.add_parser(name).add_argument("building_id")
    sh = sub.add_parser("sheet", help="contact sheet of panoramas with heading rulers")
    sh.add_argument("building_id")
    sh.add_argument("--out", required=True)
    sh.add_argument("--floor", type=int)
    sh.add_argument("--near", help='a node id prefix, or "x,y" in model coordinates')
    sh.add_argument("--radius", type=float, default=8.0)
    sh.add_argument("--nodes", help="comma-separated node id prefixes")
    sh.add_argument("--res", default="low", choices=("low", "high"))
    sh.add_argument("--max", type=int, default=8)
    vw = sub.add_parser("view", help="perspective view from a sweep along a heading")
    vw.add_argument("building_id")
    vw.add_argument("node")
    vw.add_argument("--heading", type=float, required=True)
    vw.add_argument("--pitch", type=float, default=0.0)
    vw.add_argument("--fov", type=float, default=80.0)
    vw.add_argument("--res", default="high", choices=("high", "2k"))
    vw.add_argument("--out", required=True)
    ry = sub.add_parser("ray", help="first wall and nodes along a heading from a sweep")
    ry.add_argument("building_id")
    ry.add_argument("node")
    ry.add_argument("heading", type=float)
    a = ap.parse_args()
    if a.what == "evidence":
        for p in evidence(a.building_id):
            print(p.relative_to(config.ROOT))
    elif a.what == "panos":
        g = _load(a.building_id)
        done = sum(1 for n in g["nodes"] if pano_file(g, n))
        print(f"{done} panoramas under buildings/{a.building_id}/panos/")
    elif a.what == "sheet":
        ids = contact_sheet(a.building_id, a.out, floor=a.floor, near=a.near, radius=a.radius,
                            nodes=a.nodes.split(",") if a.nodes else None, resolution=a.res, limit=a.max)
        print("\n".join(ids))
    elif a.what == "view":
        g = _load(a.building_id)
        n = resolve(g, a.node)
        view_heading(g["matterport_model_id"], n["id"], a.heading, hfov_deg=a.fov, pitch_deg=a.pitch,
                     size=768, resolution=a.res).save(a.out, quality=90)
        print(f"{n['id']} looking along {a.heading} deg -> {a.out}")
    else:
        print(json.dumps(ray(a.building_id, a.node, a.heading), indent=1))
