"""Make a stand-in caller photo from a scanned position.

    python3 -m live.demo_photo 6550930 gbqgw9                 # picks the most recognisable direction
    python3 -m live.demo_photo 6550930 gbqgw9 --heading 95
    python3 -m live.demo_photo 6550930 3ymmrb --hard          # dim, soft and zoomed in

Nobody on the team can stand in the Houston and Los Angeles buildings, so a demo photo
is a view rendered from the scan itself: a phone-shaped frame (4:3, about 78 degrees
wide, like a phone's main camera), tilted a little, softened and recompressed like a
phone upload. Without `--heading` it looks all the way round and takes the direction
with the most to see, so the photo shows a place and not a blank wall.

This is an easier case than a real photo, because the camera stands exactly on a
scanned position and the pixels come from the same panorama the photo index was cut
from. `live.evaluate --held-out` is the fairer test, and real photos the only real one.
"""
import argparse
import io
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from env import views
from env.graph import Graph


def _detail(img: Image.Image) -> float:
    """How much there is to see. Judged at a coarse scale, so large shapes (doors, counters,
    signs, a corridor running away) count and fine repeating texture (window blinds, carpet)
    does not; a frame that is mostly one flat surface or mostly blown-out window scores low."""
    small = img.convert("RGB").resize((48, 36), Image.BILINEAR)
    g = np.asarray(small.convert("L"), dtype=np.float32)
    gx, gy = np.abs(np.diff(g, axis=1)), np.abs(np.diff(g, axis=0))
    shapes = float((gx > 14).mean() + (gy > 14).mean())
    spread = float(np.percentile(g, 90) - np.percentile(g, 10)) / 255.0
    blown = float((g > 240).mean())                             # daylight through a window
    hsv = np.asarray(small.convert("HSV"), dtype=np.float32)
    colour = float(hsv[..., 1].std()) / 128.0
    return shapes * 2.0 + spread + colour - blown * 1.5


def best_heading(model_id: str, node: str, fov: float, pitch: float) -> float:
    """The direction with the most to see, out of sixteen round the compass."""
    scored = []
    for h in range(0, 360, 360 // 16):
        thumb = views.view_heading(model_id, node, h, hfov_deg=fov, pitch_deg=pitch, size=192, resolution="low")
        scored.append((_detail(thumb), h))
    return float(max(scored)[1])


def make(building_id: str, node_prefix: str, heading: float | None = None, pitch: float = -4.0,
         fov: float = 78.0, out: str | None = None, seed: int = 7, hard: bool = False) -> tuple[Path, str, float]:
    """-> (file, node id, heading). `hard` makes a photo like one taken in a hurry: zoomed in,
    tilted down, dim and soft, but still showing the place."""
    g = Graph.load(str(building_id))
    hits = [n for n in g.nodes if n.startswith(node_prefix) and g.kind(n) == "pano"]
    if len(hits) != 1:
        raise SystemExit(f"{node_prefix!r} matches {len(hits)} scan positions in building {building_id}")
    node = hits[0]
    model = g.data["matterport_model_id"]
    rng = random.Random(f"{node}:{seed}")
    if hard:
        fov, pitch = min(fov, 56.0), min(pitch, -14.0)
    if heading is None:
        heading = best_heading(model, node, fov, pitch) + rng.uniform(-9, 9)   # not exactly on an indexed view
    img = views.view_heading(model, node, heading, hfov_deg=fov, pitch_deg=pitch, size=1680,
                             resolution="2k").convert("RGB")
    w, h = img.size
    crop_h = int(w * 3 / 4)                                   # landscape 4:3
    top = (h - crop_h) // 2 + rng.randint(-30, 30)
    img = img.crop((0, top, w, top + crop_h)).rotate(rng.uniform(-2.5, 2.5), resample=Image.BICUBIC, expand=False)
    img = img.crop((40, 30, img.width - 40, img.height - 30))
    if hard:
        img = ImageEnhance.Brightness(img).enhance(0.6).filter(ImageFilter.GaussianBlur(2.2))
    else:
        img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.9, 1.08)).filter(ImageFilter.GaussianBlur(0.7))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=45 if hard else 78)
    path = Path(out) if out else Path("demo") / f"{building_id}_{node[:6]}{'_hard' if hard else ''}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    return path, node, heading


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("building_id")
    ap.add_argument("node", help="scan position (a unique prefix is enough)")
    ap.add_argument("--heading", type=float, help="degrees counterclockwise from the scan's +x axis "
                    "(default: the direction with the most to see)")
    ap.add_argument("--pitch", type=float, default=-4.0)
    ap.add_argument("--fov", type=float, default=78.0)
    ap.add_argument("--out")
    ap.add_argument("--hard", action="store_true", help="zoomed in, tilted down, dim and soft")
    a = ap.parse_args()
    path, node, heading = make(a.building_id, a.node, a.heading, a.pitch, a.fov, a.out, hard=a.hard)
    print(f"{path}  taken at {node}, heading {heading:.0f}")
