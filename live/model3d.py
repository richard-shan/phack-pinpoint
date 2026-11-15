"""A 3D model of the scan for the console's 3D route page (`app/live-3d.html`).

    python3 -m live.model3d 6550930            # one building
    python3 -m live.model3d --all              # every building with a graph

Matterport serves each scan's geometry as a 50,000-triangle mesh without textures. This
downloads it, turns it to the viewer's axes (y up), and writes what the page needs to
`app/assets/real/models/<building_id>/` (git-ignored, served by the console):

    mesh.json       positions and triangle indices
    floor<n>.png    the scan's top-down floor image for each level, with transparency,
                    laid on the floor of the model so the rooms are recognisable
    model.json      floor heights, where each floor image sits, the building's name

`route_json()` writes the route of one incident in the same axes; the page draws it as
a thick line that stays visible through walls.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from env import config, matterport as mp
from env.graph import Graph, list_buildings

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "app" / "assets" / "real" / "models"
VENDOR = ROOT / "app" / "assets" / "real" / "vendor"
THREE_URL = "https://unpkg.com/three@0.160.0/build/three.module.min.js"     # pinned release
MAX_PLAN_PX = 2048


def sdk(p) -> list[float]:
    """Scan axes (z up) to viewer axes (y up)."""
    return [round(float(p[0]), 3), round(float(p[2]), 3), round(float(-p[1]), 3)]


def _mesh_url(model_id: str) -> str:
    q = '{ model(id: "%s") { assets { meshes { format resolution status url } } } }' % model_id
    meshes = [m for m in mp._gql(q)["model"]["assets"]["meshes"] or [] if m.get("format") == "obj" and m.get("url")]
    if not meshes:
        raise RuntimeError(f"Matterport offers no mesh for scan {model_id}")
    return meshes[0]["url"]


def _parse_obj(text: str):
    verts, faces = [], []
    for line in text.splitlines():
        if line.startswith("v "):
            _, x, y, z = line.split()[:4]
            verts.append((float(x), float(y), float(z)))
        elif line.startswith("f "):
            idx = [int(tok.split("/")[0]) - 1 for tok in line.split()[1:]]
            for k in range(1, len(idx) - 1):               # fan out anything that is not a triangle
                faces.append((idx[0], idx[k], idx[k + 1]))
    return np.array(verts, dtype=np.float64), np.array(faces, dtype=np.int64)


def _wall_mesh(g: Graph, model_id: str):
    """A model built from the scan's wall plan, for scans whose mesh Matterport does not
    serve: each wall drawn as a panel from the floor up, with gaps at doorways. Returns
    (positions in viewer axes, triangle indices) or None when the scan has no wall plan."""
    walls = mp.get_walls(model_id)
    heights = {}
    for n in g.nodes:
        if g.kind(n) == "pano" and g.xyz(n):
            heights.setdefault(g.nodes[n].get("floor") or 0, []).append(g.xyz(n)[2])
    levels = {seq: float(np.median(zs)) for seq, zs in heights.items()}
    order = sorted(levels.values())
    pos, idx = [], []

    def panel(a, b, z0, z1):
        base = len(pos)
        for x, y, z in ((a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)):
            pos.append((x, z, -y))
        idx.extend((base, base + 1, base + 2, base, base + 2, base + 3,          # seen from one side
                    base, base + 2, base + 1, base, base + 3, base + 2))         # and from the other

    for seq, segs in walls.items():
        if seq not in levels:
            continue
        z0 = levels[seq]
        above = [z for z in order if z > z0 + 1.0]
        z1 = z0 + min(2.5, (above[0] - z0 - 0.4) if above else 2.5)
        for w in segs:
            if w.get("type") != "wall" or w["length"] < 0.05:
                continue
            (ax, ay), (bx, by), length = w["a"], w["b"], w["length"]
            at = lambda d: (ax + (bx - ax) * d / length, ay + (by - ay) * d / length)
            gaps = sorted((max(0.0, o["center_m"] - o["width"] / 2), min(length, o["center_m"] + o["width"] / 2))
                          for o in w.get("openings") or [] if o.get("type") in ("doorway", "opening") and o.get("width"))
            cursor = 0.0
            for g0, g1 in gaps:
                if g0 - cursor > 0.05:
                    panel(at(cursor), at(g0), z0, z1)
                cursor = max(cursor, g1)
            if length - cursor > 0.05:
                panel(at(cursor), at(length), z0, z1)
    if not pos:
        return None
    return np.array(pos, dtype=np.float64), np.array(idx, dtype=np.int64).reshape(-1, 3)


def fetch_three() -> Path:
    """three.js, served from the console's own origin (its pages may not load scripts from elsewhere)."""
    out = VENDOR / "three.module.min.js"
    if not out.exists():
        r = requests.get(THREE_URL, timeout=120)
        r.raise_for_status()
        if b"WebGLRenderer" not in r.content:
            raise RuntimeError("the three.js download does not look like three.js")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
    return out


def build(building_id: str, refresh: bool = False) -> dict:
    """Write the model files for one building. -> a short summary"""
    g = Graph.load(str(building_id))
    model_id = g.data.get("matterport_model_id")
    if not model_id:
        raise ValueError(f"building {building_id} has no scan behind its graph")
    out = MODELS / str(building_id)
    out.mkdir(parents=True, exist_ok=True)
    mesh_path = out / "mesh.json"
    source = "Matterport 50k mesh (geometry only) with the scan's floor images laid on each floor"
    if refresh or not mesh_path.exists():
        try:
            r = requests.get(_mesh_url(model_id), timeout=300)
            r.raise_for_status()
            verts, faces = _parse_obj(r.content.decode("utf-8", "replace"))
            if not len(verts) or not len(faces):
                raise RuntimeError(f"the mesh for scan {model_id} is empty")
            pos = np.stack([verts[:, 0], verts[:, 2], -verts[:, 1]], axis=1)      # y up
        except RuntimeError:                               # no mesh served for this scan
            built = _wall_mesh(g, model_id)
            if built is None:
                raise
            pos, faces = built
            source = "walls drawn from the scan's wall plan (Matterport serves no mesh for this scan)"
        mesh_path.write_text(json.dumps({
            "positions": [round(float(v), 3) for v in pos.reshape(-1)],
            "indices": [int(i) for i in faces.reshape(-1)],
            "bounds": {"min": [round(float(v), 2) for v in pos.min(0)], "max": [round(float(v), 2) for v in pos.max(0)]},
            "source": source,
        }, separators=(",", ":")))
    else:
        source = json.loads(mesh_path.read_text()).get("source", source)
    floors = []
    for seq_s, fp in sorted((g.data.get("floorplans") or {}).items(), key=lambda kv: int(kv[0])):
        seq = int(seq_s)
        on_floor = [g.xyz(n)[2] for n in g.nodes if g.kind(n) == "pano" and (g.nodes[n].get("floor") or 0) == seq and g.xyz(n)]
        if not on_floor:
            continue
        name = next((g.nodes[n].get("floor_name") for n in g.nodes
                     if (g.nodes[n].get("floor") or 0) == seq and g.nodes[n].get("floor_name")), f"Floor {seq + 1}")
        entry = {"sequence": seq, "name": name, "height": round(float(np.median(on_floor)), 3)}
        try:                                               # the floor image with a transparent background
            plan = mp.get_floorplan(model_id, seq, flags=("photogramy", "alpha"), fmt="png")
            if not plan or not plan.get("resolution") or plan["origin"][0] is None:
                raise RuntimeError("no transparent floor image for this level")
            img = Image.open(plan["path"]).convert("RGBA")
            ppm, (ox, oy) = plan["resolution"], plan["origin"]
            w_m, h_m = img.width / ppm, img.height / ppm
            img.thumbnail((MAX_PLAN_PX, MAX_PLAN_PX))
            img.save(out / f"floor{seq}.png")
            # corners in viewer axes: image x runs along scan +x, image y runs along scan -y
            entry["plan"] = {"url": f"/assets/real/models/{building_id}/floor{seq}.png",
                             "x0": round(ox, 3), "x1": round(ox + w_m, 3),
                             "z0": round(-(oy + h_m), 3), "z1": round(-oy, 3)}
        except Exception as exc:                           # a model is still useful without its floor image
            entry["plan_error"] = str(exc)[:160]
        floors.append(entry)
    meta = {"buildingId": str(building_id), "name": g.data.get("name") or str(building_id), "modelId": model_id,
            "mesh": f"/assets/real/models/{building_id}/mesh.json", "floors": floors, "source": source}
    (out / "model.json").write_text(json.dumps(meta, indent=1))
    return {"building_id": str(building_id), "floors": len(floors), "mesh_kb": mesh_path.stat().st_size // 1024,
            "floor_images": sum("plan" in f for f in floors)}


def route_json(building_id: str, path: list[str], *, title: str = "", lines: list[str] | None = None,
               caller: str = "") -> dict:
    """One route for the 3D page: points in viewer axes, with what each end is."""
    g = Graph.load(str(building_id))
    path = [n for n in path if n in g.nodes and g.xyz(n)]
    door = next((n for n in path if g.nodes[n].get("is_entrance")), None)
    staging = next((n for n in path if g.kind(n) == "staging"), None)
    legs = []
    for a, b in zip(path, path[1:]):
        e = g.edge(a, b) or {}
        legs.append({"a": sdk(g.xyz(a)), "b": sdk(g.xyz(b)),
                     "kind": e.get("type", "open"), "outside": e.get("type") in ("exterior_foot", "exterior_vehicle")})
    mark = lambda n, kind, label: {"kind": kind, "position": sdk(g.xyz(n)), "label": label,
                                   "floor": g.nodes[n].get("floor") or 0} if n else None
    target = path[-1] if path else None
    markers = [m for m in (mark(staging, "stage", g.label(staging) if staging else ""),
                           mark(door, "enter", g.label(door) if door else ""),
                           mark(target, "caller", caller or (g.label(target) or g.room(target) or "") if target else "")) if m]
    return {"buildingId": str(building_id), "model": f"/assets/real/models/{building_id}/model.json",
            "title": title, "lines": lines or [], "legs": legs, "markers": markers}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("building_id", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="download the mesh again")
    a = ap.parse_args()
    todo = [b for b in list_buildings() if b != "toy"] if a.all else [a.building_id]
    if not todo or todo == [None]:
        ap.error("give a building id or --all")
    print(f"three.js: {fetch_three().relative_to(ROOT)}")
    for b in todo:
        try:
            print(json.dumps(build(b, a.refresh)))
        except Exception as exc:
            print(f"{b}: no 3D model ({type(exc).__name__}: {str(exc)[:160]})")


if __name__ == "__main__":
    main()
