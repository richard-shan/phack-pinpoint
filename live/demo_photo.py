"""Make a stand-in caller photo from a scanned position.

    python3 -m live.demo_photo 6550930 9nydrb --heading 200 --out demo/fannin_lobby.jpg

Nobody on the team can stand in these buildings, so a demo photo is a view rendered
from the scan itself: a phone-shaped (3:4) frame at a heading, tilt and zoom that are
not among the views the photo index holds, then softened and recompressed like a phone
upload. It is an easier case than a real photo, because the camera stands exactly on a
scanned position; the file name and the run output say where it was taken.
"""
import argparse
import io
import random
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

from env import views
from env.graph import Graph


def make(building_id: str, node_prefix: str, heading: float | None = None, pitch: float = -7.0,
         fov: float = 68.0, out: str | None = None, seed: int = 7, hard: bool = False) -> tuple[Path, str, float]:
    """-> (file, node id, heading). `hard` makes a photo like one taken in a hurry: zoomed
    in, pointed at the floor, dark and blurred, so the match is less certain."""
    if hard:
        fov, pitch = min(fov, 46.0), min(pitch, -28.0)
    g = Graph.load(str(building_id))
    hits = [n for n in g.nodes if n.startswith(node_prefix) and g.kind(n) == "pano"]
    if len(hits) != 1:
        raise SystemExit(f"{node_prefix!r} matches {len(hits)} scan positions in building {building_id}")
    node = hits[0]
    rng = random.Random(f"{node}:{seed}")
    if heading is None:
        heading = rng.uniform(0, 360)
    img = views.view_heading(g.data["matterport_model_id"], node, heading, hfov_deg=fov, pitch_deg=pitch,
                             size=1200, resolution="2k").convert("RGB")
    w, h = img.size
    crop_w = int(h * 3 / 4)                                   # portrait phone frame
    left = (w - crop_w) // 2 + rng.randint(-40, 40)
    img = img.crop((left, 0, left + crop_w, h)).rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, expand=False)
    img = img.crop((30, 40, img.width - 30, img.height - 40))
    if hard:
        img = ImageEnhance.Brightness(img).enhance(0.5).filter(ImageFilter.GaussianBlur(3.0))
    else:
        img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.85, 1.1)).filter(ImageFilter.GaussianBlur(0.8))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=35 if hard else 72)
    path = Path(out) if out else Path("demo") / f"{building_id}_{node[:6]}_h{round(heading):03d}{'_hard' if hard else ''}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    return path, node, heading


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("building_id")
    ap.add_argument("node", help="scan position (a unique prefix is enough)")
    ap.add_argument("--heading", type=float, help="degrees counterclockwise from the scan's +x axis (default: random)")
    ap.add_argument("--pitch", type=float, default=-7.0)
    ap.add_argument("--fov", type=float, default=68.0)
    ap.add_argument("--out")
    ap.add_argument("--hard", action="store_true", help="zoomed in, tilted down, dark and blurred")
    a = ap.parse_args()
    path, node, heading = make(a.building_id, a.node, a.heading, a.pitch, a.fov, a.out, hard=a.hard)
    print(f"{path}  taken at {node}, heading {heading:.0f}")
