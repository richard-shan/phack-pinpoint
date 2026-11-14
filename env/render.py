"""Draw a building graph over its floor plan, one image per floor, to check it by eye.

    python -m env.render <building_id> [--path n1,n2,...]

Blue: level links. Orange: door links. Red: stairs. Green squares: entrance candidates.
Grey lines: walls from the scan, with doorways left as gaps in yellow.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from . import config
from . import matterport as mp

COLORS = {"open": (40, 110, 255), "door": (255, 140, 0), "locked_door": (200, 0, 200),
          "stairs": (230, 30, 30), "elevator": (150, 0, 200),
          "exterior_foot": (0, 160, 90), "exterior_vehicle": (90, 90, 90)}


def render_building(building_id: str, path: list[str] | None = None, scale: float = 1.0,
                    labels: bool = False) -> list[Path]:
    gpath = config.BUILDINGS / str(building_id) / "graph.json"
    g = json.loads(gpath.read_text())
    nodes = {n["id"]: n for n in g["nodes"]}
    walls = mp.get_walls(g["matterport_model_id"]) if g.get("matterport_model_id") else {}
    on_path = set(zip(path, path[1:])) | set(zip(path[1:], path)) if path else set()
    out = []
    for f in g["floors"]:
        seq = f["floor"]
        fp = (g.get("floorplans") or {}).get(str(seq))
        if not fp:
            continue
        img = Image.open(config.ROOT / fp["path"]).convert("RGB")
        ppm, (ox, oy), h = fp["pixels_per_meter"], fp["origin_xy"], img.height

        def px(x, y):
            return ((x - ox) * ppm, h - (y - oy) * ppm)

        d = ImageDraw.Draw(img)
        for w in walls.get(seq) or []:
            col = (120, 120, 120) if w["type"] == "wall" else (190, 190, 255)
            d.line([px(*w["a"]), px(*w["b"])], fill=col, width=3)
            ux, uy = (w["b"][0] - w["a"][0]) / (w["length"] or 1), (w["b"][1] - w["a"][1]) / (w["length"] or 1)
            for o in w["openings"]:
                a = (w["a"][0] + ux * (o["center_m"] - o["width"] / 2), w["a"][1] + uy * (o["center_m"] - o["width"] / 2))
                b = (w["a"][0] + ux * (o["center_m"] + o["width"] / 2), w["a"][1] + uy * (o["center_m"] + o["width"] / 2))
                d.line([px(*a), px(*b)], fill=(255, 230, 0) if o["type"] == "doorway" else (0, 220, 220), width=5)
        for e in g["edges"]:
            a, b = nodes[e["a"]], nodes[e["b"]]
            if seq not in (a["floor"], b["floor"]):
                continue
            hot = (e["a"], e["b"]) in on_path
            d.line([px(*a["xyz"][:2]), px(*b["xyz"][:2])],
                   fill=(0, 200, 0) if hot else COLORS.get(e["type"], (0, 0, 0)), width=9 if hot else 3)
        for n in nodes.values():
            if n["floor"] != seq:
                continue
            x, y = px(*n["xyz"][:2])
            if n.get("is_entrance"):
                d.rectangle([x - 11, y - 11, x + 11, y + 11], fill=(0, 190, 60), outline=(0, 0, 0), width=2)
            elif n.get("kind") in ("staging", "arrival"):
                continue
            else:
                r = 6
                col = (150, 0, 200) if n.get("kind") == "elevator" else (20, 20, 20)
                d.ellipse([x - r, y - r, x + r, y + r], fill=col, outline=(255, 255, 255))
            if labels:
                d.text((x + 8, y - 6), n["id"][:5], fill=(255, 255, 0), stroke_width=2, stroke_fill=(0, 0, 0))
        if scale != 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        p = gpath.with_name(f"graph_floor{seq}{'_labels' if labels else ''}.jpg")
        img.save(p, quality=88)
        out.append(p)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("building_id")
    ap.add_argument("--path", help="comma-separated node ids to highlight")
    ap.add_argument("--labels", action="store_true", help="write the first 5 characters of each node id")
    a = ap.parse_args()
    for p in render_building(a.building_id, a.path.split(",") if a.path else None, labels=a.labels):
        print(p)
