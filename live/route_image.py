"""A picture of the route to the caller, drawn on the scan's floor plan.

    python3 -m live.route_image agent/incidents/<id>                # the route of a finished incident
    python3 -m live.route_image --building 6550930 --to gbqgw9      # best door to a position

One panel per floor the route passes through: the floor plan, the staging spot, the door,
the path with arrows, and the caller's position. The caller's photo is inset when given.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from env import config, tools as A
from env.graph import Graph

RED, GREEN, BLUE, WHITE, INK = (214, 40, 57), (38, 160, 90), (41, 98, 160), (255, 255, 255), (24, 28, 34)
PAD_M = 5.0
OUT_W = 1120            # map width; the legend column sits to its right
SIDE_W = 400


def _font(size: int):
    for name in ("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _plan(g: Graph, seq: int):
    """Floor plan image and the function from scan x, y to its pixels."""
    fp = (g.data.get("floorplans") or {}).get(str(seq))
    if fp:
        path = config.ROOT / fp["path"]
        if not path.exists():
            try:                                  # not cached on this machine yet
                from env import matterport as mp
                mp.get_floorplan(g.data["matterport_model_id"], seq)
            except Exception:
                pass
        if path.exists():
            img = Image.open(path).convert("RGB")
            ppm, (ox, oy), h = fp["pixels_per_meter"], fp["origin_xy"], img.height
            return img, ppm, (lambda x, y: ((x - ox) * ppm, h - (y - oy) * ppm))
    pts = [g.xyz(n) for n in g.nodes if g.xyz(n) and (g.nodes[n].get("floor") or 0) == seq]
    ox, oy = min(p[0] for p in pts) - 3, min(p[1] for p in pts) - 3
    w, h = max(p[0] for p in pts) + 3 - ox, max(p[1] for p in pts) + 3 - oy
    ppm = 40.0
    return Image.new("RGB", (int(w * ppm), int(h * ppm)), (52, 56, 66)), ppm, (lambda x, y: ((x - ox) * ppm, h * ppm - (y - oy) * ppm))


def _north(g: Graph, seq: int):
    """Which way north points on this floor's plan, as a unit vector in image axes, from the
    lat/lon of the two scanned positions farthest apart. The scan's rotation is reliable
    even where its placement on the map is tens of meters off."""
    pts = [(g.xyz(n), g.nodes[n].get("latlon")) for n in g.nodes
           if g.kind(n) == "pano" and (g.nodes[n].get("floor") or 0) == seq and g.xyz(n) and g.nodes[n].get("latlon")]
    if len(pts) < 2:
        return None
    a = min(pts, key=lambda p: p[0][0] + p[0][1])
    b = max(pts, key=lambda p: math.dist(p[0][:2], a[0][:2]))
    if math.dist(a[0][:2], b[0][:2]) < 5:
        return None
    east = (b[1][1] - a[1][1]) * math.cos(math.radians(a[1][0])) * 111320.0
    north = (b[1][0] - a[1][0]) * 111320.0
    turn = math.atan2(b[0][1] - a[0][1], b[0][0] - a[0][0]) - math.atan2(north, east)   # scan angle = map angle + turn
    ang = math.pi / 2 + turn                                # north, in scan axes
    return (math.cos(ang), -math.sin(ang))                  # image y runs down


def _label(d: ImageDraw.ImageDraw, xy, text: str, fill, font, anchor="l"):
    x, y = xy
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x0 = x + 16 if anchor == "l" else x - 16 - w - 16
    y0 = y - h / 2 - 8
    d.rounded_rectangle((x0, y0, x0 + w + 16, y0 + h + 16), 8, fill=WHITE, outline=fill, width=3)
    d.text((x0 + 8 - box[0], y0 + 8 - box[1]), text, fill=INK, font=font)


def _arrow(d, a, b, color, size):
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    pts = [(mid[0] + size * math.cos(ang), mid[1] + size * math.sin(ang)),
           (mid[0] + size * math.cos(ang + 2.5), mid[1] + size * math.sin(ang + 2.5)),
           (mid[0] + size * math.cos(ang - 2.5), mid[1] + size * math.sin(ang - 2.5))]
    d.polygon(pts, fill=WHITE, outline=color)


def _panel(g: Graph, seq: int, path: list[str], target: str, door: str | None, staging: str | None,
           min_ratio: float = 0.0) -> Image.Image:
    plan, ppm, to_px = _plan(g, seq)
    nodes = [n for n in path if (g.nodes[n].get("floor") or 0) == seq and g.xyz(n)]
    pts = [to_px(*g.xyz(n)[:2]) for n in nodes]
    pad = PAD_M * ppm
    x0, y0 = min(p[0] for p in pts) - pad, min(p[1] for p in pts) - pad
    x1, y1 = max(p[0] for p in pts) + pad, max(p[1] for p in pts) + pad
    short = 16.0 * ppm - (x1 - x0)                        # never zoom in closer than about 16 m across
    if short > 0:
        x0, x1 = x0 - short / 2, x1 + short / 2
    if max(plan.width, plan.height) <= 3.0 * max(x1 - x0, y1 - y0):   # small floor: show all of it
        x0, y0, x1, y1 = min(x0, 0), min(y0, 0), max(x1, plan.width), max(y1, plan.height)
    if (x1 - x0) < 2.2 * (y1 - y0):                       # keep panels wide enough for labels
        cx, half = (x0 + x1) / 2, 1.1 * (y1 - y0)
        x0, x1 = cx - half, cx + half
    if (y1 - y0) < min_ratio * (x1 - x0):                 # show more of the floor to fill the sheet
        cy, half = (y0 + y1) / 2, min_ratio * (x1 - x0) / 2
        y0, y1 = cy - half, cy + half
    bg = plan.getpixel((2, 2))
    canvas = Image.new("RGB", (int(x1 - x0), int(y1 - y0)), bg)
    canvas.paste(plan, (int(-x0), int(-y0)))
    scale = OUT_W / canvas.width
    canvas = canvas.resize((OUT_W, max(1, int(canvas.height * scale))), Image.LANCZOS)
    pts = [((p[0] - x0) * scale, (p[1] - y0) * scale) for p in pts]
    d = ImageDraw.Draw(canvas)
    font = _font(26)
    if len(pts) > 1:
        d.line(pts, fill=WHITE, width=18, joint="curve")
        d.line(pts, fill=RED, width=10, joint="curve")
        step = max(1, len(pts) // 8)
        for a, b in list(zip(pts, pts[1:]))[::step]:
            if math.dist(a, b) > 30:
                _arrow(d, a, b, RED, 15)
    by_node = dict(zip(nodes, pts))
    side = lambda p: "l" if p[0] < OUT_W * 0.7 else "r"
    flip = {"l": "r", "r": "l"}
    caller_side = side(by_node[target]) if target in by_node else None
    if target in by_node:
        p = by_node[target]
        d.ellipse((p[0] - 30, p[1] - 30, p[0] + 30, p[1] + 30), outline=RED, width=6)
        d.ellipse((p[0] - 13, p[1] - 13, p[0] + 13, p[1] + 13), fill=RED, outline=WHITE, width=4)
        _label(d, (p[0] + (22 if caller_side == "l" else -22), p[1]), "Caller", RED, font, caller_side)
    else:                                                  # the route leaves this floor here
        p = pts[-1]
        nxt = path[path.index(nodes[-1]) + 1] if nodes[-1] != path[-1] else None
        how = (g.edge(nodes[-1], nxt) or {}).get("type", "stairs") if nxt else "stairs"
        _label(d, (p[0], p[1] - 44), f"{how.capitalize()} to {g.nodes[target].get('floor_name') or 'the next floor'}",
               RED, font, side(p))
    if staging in by_node:
        p = by_node[staging]
        d.ellipse((p[0] - 16, p[1] - 16, p[0] + 16, p[1] + 16), fill=BLUE, outline=WHITE, width=4)
        _label(d, (p[0], p[1] - 34), "Stage", BLUE, font, side(p))
    if door in by_node:                                    # drawn last so the caller ring never hides it
        p = by_node[door]
        d.rectangle((p[0] - 15, p[1] - 15, p[0] + 15, p[1] + 15), fill=GREEN, outline=WHITE, width=4)
        close = target in by_node and math.dist(p, by_node[target]) < 150
        where = flip[caller_side] if close else side(p)    # keep clear of the Caller label
        _label(d, (p[0], p[1] + (0 if close else 34)), "Enter", GREEN, font, where)
    north = _north(g, seq)
    if north:                                              # the plan is in the scan's axes, not north-up
        cx, cy, r = OUT_W - 70, 70, 34
        tip, tail = (cx + r * north[0], cy + r * north[1]), (cx - r * north[0], cy - r * north[1])
        d.ellipse((cx - 50, cy - 50, cx + 50, cy + 50), fill=INK, outline=WHITE, width=3)
        d.line((tail, tip), fill=WHITE, width=6)
        ang = math.atan2(north[1], north[0])
        d.polygon([tip, (tip[0] + 18 * math.cos(ang + 2.6), tip[1] + 18 * math.sin(ang + 2.6)),
                   (tip[0] + 18 * math.cos(ang - 2.6), tip[1] + 18 * math.sin(ang - 2.6))], fill=WHITE)
        px, py = -north[1], north[0]                       # beside the shaft, not on it
        d.text((cx - 9 + 22 * px - 8 * north[0], cy - 14 + 22 * py - 8 * north[1]), "N", fill=(255, 210, 90), font=font)
    name = next((g.nodes[n].get("floor_name") for n in nodes if g.nodes[n].get("floor_name")), f"Floor {seq + 1}")
    d.rounded_rectangle((16, canvas.height - 62, 16 + d.textlength(name, font=font) + 28, canvas.height - 16), 8, fill=INK)
    d.text((30, canvas.height - 54), name, fill=WHITE, font=font)
    return canvas


def _wrap(d, text: str, font, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def draw(building_id: str, path: list[str], out: str | Path, *, title: str = "", subtitle: str = "",
         photo: str | None = None, caller: str = "") -> Path:
    """Draw `path` (staging spot or door first, caller last) and save it. -> the file"""
    g = Graph.load(str(building_id))
    path = [n for n in path if n in g.nodes]
    if len(path) < 1:
        raise ValueError("no route to draw")
    target = path[-1]
    door = next((n for n in path if g.nodes[n].get("is_entrance")), None)
    staging = next((n for n in path if g.kind(n) == "staging"), None)
    floors = []
    for n in path:
        f = g.nodes[n].get("floor") or 0
        if f not in floors:
            floors.append(f)
    # legend column: the photo, then what each marker is
    side = Image.new("RGB", (SIDE_W, 2400), (245, 246, 248))
    d = ImageDraw.Draw(side)
    y, inner = 22, SIDE_W - 44
    small, bold = _font(21), _font(23)
    if photo and Path(photo).exists():
        try:
            ph = Image.open(photo).convert("RGB")
            ph.thumbnail((inner, 420))
            side.paste(ph, (22 + (inner - ph.width) // 2, y))
            y += ph.height + 6
            d.text((22, y), "Caller's photo", fill=(90, 96, 106), font=small)
            y += 44
        except OSError:
            pass
    place = caller or ", ".join(x for x in (g.nodes[target].get("floor_name"), g.room(target), g.label(target)) if x)
    for color, shape, head, text in ((BLUE, "dot", "Stage", g.label(staging) if staging else None),
                                     (GREEN, "box", "Enter", g.label(door) if door else None),
                                     (RED, "dot", "Caller", place or f"scan position {target[:6]}")):
        if not text:
            continue
        (d.rectangle if shape == "box" else d.ellipse)((22, y + 4, 44, y + 26), fill=color)
        d.text((56, y), head, fill=INK, font=bold)
        y += 32
        for line in _wrap(d, text[:1].upper() + text[1:], small, inner - 34):
            d.text((56, y), line, fill=(60, 66, 76), font=small)
            y += 27
        y += 14
    for line in _wrap(d, subtitle, small, inner):
        d.text((22, y), line, fill=INK, font=small)
        y += 27
    side_h = y + 20

    panels = [_panel(g, f, path, target, door, staging, min_ratio=side_h / len(floors) / OUT_W) for f in floors]
    map_h = sum(p.height for p in panels) + 8 * (len(panels) - 1)
    head_h = 76
    body_h = max(map_h, side_h)
    sheet = Image.new("RGB", (OUT_W + SIDE_W, head_h + body_h), INK)
    ImageDraw.Draw(sheet).text((24, 16), title or (g.data.get("name") or str(building_id)), fill=WHITE, font=_font(36))
    sheet.paste(Image.new("RGB", (SIDE_W, body_h), (245, 246, 248)), (OUT_W, head_h))
    sheet.paste(side.crop((0, 0, SIDE_W, min(side_h, body_h))), (OUT_W, head_h))
    y = head_h
    for p in panels:
        sheet.paste(p, (0, y))
        y += p.height + 8
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return out


def for_incident(incident_dir: str | Path, out: str | Path | None = None) -> Path:
    """The route picture for a finished incident folder (agent/incidents/<id>)."""
    d = Path(incident_dir)
    intake = json.loads((d / "intake.json").read_text())
    route = json.loads((d / "route.json").read_text())
    loc = json.loads((d / "location.json").read_text())
    foot = route.get("foot") or {}
    b = str(intake["building_id"])
    g = Graph.load(b)
    door = g.label(foot.get("entrance_id")) if foot.get("entrance_id") in g.nodes else foot.get("entrance_id")
    sub = f"On foot: about {round(foot['seconds'])} s, {round(foot.get('meters') or 0)} m." if foot.get("seconds") else ""
    st = route.get("stretcher") or {}
    if st.get("seconds") and st.get("entrance_id"):
        same = st.get("entrance_id") == foot.get("entrance_id")
        sub += f" Stretcher: about {round(st['seconds'])} s" + ("." if same else f", through {g.label(st['entrance_id']) or st['entrance_id']}.")
    elif foot.get("seconds"):
        sub += " A stretcher cannot reach this position."
    target = (foot.get("path") or [None])[-1]
    place = ", ".join(x for x in (g.nodes[target].get("floor_name"), g.room(target), g.label(target)) if x) if target in g.nodes else ""
    caller = f"{place} (confidence {loc.get('confidence', 0) * 100:.0f}%)" if place else ""
    photo = intake.get("caller_photo_path")
    return draw(b, foot.get("path") or [], out or d / "route.jpg", title="Route to the caller: " + intake.get("address", b),
                subtitle=sub, photo=photo if photo and Path(photo).exists() else None, caller=caller)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("incident", nargs="?", help="an incident folder under agent/incidents/")
    ap.add_argument("--building")
    ap.add_argument("--to", help="scan position (a unique prefix is enough)")
    ap.add_argument("--profile", default="foot", choices=list(config.PROFILES))
    ap.add_argument("--photo")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.incident:
        print(for_incident(a.incident, a.out))
    else:
        if not (a.building and a.to):
            ap.error("give an incident folder, or --building and --to")
        g = Graph.load(a.building)
        hits = [n for n in g.nodes if n.startswith(a.to)]
        if len(hits) != 1:
            ap.error(f"{a.to!r} matches {len(hits)} positions")
        r = A.best_entry(a.building, hits[0], a.profile)
        if not r.get("path"):
            ap.error(r.get("reason") or "no route")
        print(draw(a.building, r["path"], a.out or f"demo/route_{a.building}_{hits[0][:6]}.jpg",
                   title="Route to the caller: " + (g.data.get("name") or a.building),
                   subtitle=f"{a.profile.capitalize()}: about {round(r['seconds'])} s, {round(r['meters'])} m, "
                            f"through {r.get('label') or r['entrance_id']}.", photo=a.photo))
