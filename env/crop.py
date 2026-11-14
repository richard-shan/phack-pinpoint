"""Directional crops: a perspective view from sweep A toward neighbor B.

Matterport serves each panorama as six cube faces (`skybox0..5`). This module samples
only the faces a view needs into a pinhole image looking from A toward B, pitched down
so the floor/threshold between them is in frame. That crop is what the vision model sees.

Convention:

  Pano-local frame: Z up. Cube faces in that frame
      face 0 = up    (+Z)   face 1's side along its bottom edge
      face 1 = +X    face 2 = -Y    face 3 = -X    face 4 = +Y   (each 90 deg clockwise
                                                               of the last, seen from above)
      face 5 = down  (-Z)   face 1's side along its top edge
  Quaternion: `rotation` {x, y, z, w} rotates pano-local directions into world
      directions: d_world = R(q) d_local, so d_local = R(q)^T d_world.

Calibration evidence:
  * Face layout: pixel continuity across all 12 cube edges on 23 sweeps of 21fi9ao6CEi;
    the chosen layout's seam error was ~6 vs >= 43 for every alternative orientation.
  * Quaternion and yaw offset: for each neighbor pair, A looking toward B should match B
    looking the same way, magnified. Mean best-zoom NCC over all 8 (offset x q / q^-1)
    hypotheses: this one 0.43 (21fi9ao6CEi, 62 directed pairs), 0.55 (Nuk92vTCkdP, 212),
    0.54 (zysxrPL4hEr, 6); every other hypothesis <= 0.34.
  * Viewed by eye: 11 pairs on 4 models, bearings in all quadrants, rotation differences
    up to 171 deg, plus stair edges between floors.
  Locked by perception/tests/test_crop.py (synthetic colored cube).
"""
from __future__ import annotations

import argparse
import math
from typing import Callable

import numpy as np
from PIL import Image

X, Y, Z = np.eye(3)

# face index -> (forward, right, down) in the pano-local frame. A pixel at normalized
# (a, b) in [-1, 1]^2 (a to the right, b downward) looks along forward + a*right + b*down.
FACES: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {
    0: (+Z, -Y, +X),
    1: (+X, -Y, -Z),
    2: (-Y, -X, -Z),
    3: (-X, +Y, -Z),
    4: (+Y, +X, -Z),
    5: (-Z, -Y, -X),
}
_FWD = np.stack([FACES[i][0] for i in range(6)])

# Calibration knobs (kept as data so the calibration script can test alternatives).
FACE1_YAW_DEG = 0.0        # extra yaw of the face layout inside the pano-local frame
QUAT_LOCAL_TO_WORLD = True  # d_world = R(q) d_local


def quat_to_matrix(quat) -> np.ndarray:
    """Rotation matrix of a unit quaternion given as [x, y, z, w]."""
    x, y, z, w = (float(v) for v in quat)
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def _rz(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def world_to_local(quat, face1_yaw_deg: float | None = None,
                   local_to_world: bool | None = None) -> np.ndarray:
    """3x3 matrix M with d_local = M @ d_world for a pano with this rotation."""
    yaw = FACE1_YAW_DEG if face1_yaw_deg is None else face1_yaw_deg
    l2w = QUAT_LOCAL_TO_WORLD if local_to_world is None else local_to_world
    r = quat_to_matrix(quat)
    return _rz(-yaw) @ (r.T if l2w else r)


def view_rays(direction_world, hfov_deg: float = 90, pitch_deg: float = -15,
              size: int | tuple[int, int] = 768) -> np.ndarray:
    """World-space ray per output pixel, shape (H, W, 3).

    The camera's yaw comes from the horizontal part of `direction_world`; its pitch is
    the elevation of `direction_world` plus `pitch_deg` (negative looks down). No roll.
    """
    w, h = (size, size) if isinstance(size, int) else size
    d = np.asarray(direction_world, dtype=float)
    horiz = math.hypot(d[0], d[1])
    yaw = math.atan2(d[1], d[0]) if horiz > 1e-9 else 0.0
    elev = math.degrees(math.atan2(d[2], horiz)) if (horiz > 1e-9 or abs(d[2]) > 1e-9) else 0.0
    pitch = math.radians(max(-89.0, min(89.0, elev + pitch_deg)))
    fwd = np.array([math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)])
    right = np.array([math.sin(yaw), -math.cos(yaw), 0.0])       # fwd x Z, horizontal
    up = np.cross(right, fwd)
    tx = math.tan(math.radians(hfov_deg) / 2)
    ty = tx * h / w
    xs = ((np.arange(w) + 0.5) / w * 2 - 1) * tx
    ys = ((np.arange(h) + 0.5) / h * 2 - 1) * ty
    rays = fwd[None, None] + xs[None, :, None] * right[None, None] - ys[:, None, None] * up[None, None]
    return rays / np.linalg.norm(rays, axis=-1, keepdims=True)


def face_of(local_dirs: np.ndarray) -> np.ndarray:
    """Cube face index hit by each local-frame direction (..., 3)."""
    return np.argmax(local_dirs @ _FWD.T, axis=-1)


def faces_for_view(quat, direction_world, hfov_deg=90, pitch_deg=-15) -> list[int]:
    """Which faces a view samples (cheap: uses a coarse ray grid incl. the border)."""
    rays = view_rays(direction_world, hfov_deg, pitch_deg, 33).reshape(-1, 3)
    return sorted(int(i) for i in np.unique(face_of(rays @ world_to_local(quat).T)))


def sample_cube(get_face_fn: Callable[[int], Image.Image], local_dirs: np.ndarray) -> np.ndarray:
    """Bilinear sample of the cube along local-frame directions (H, W, 3) -> uint8 (H, W, 3).

    Calls get_face_fn(index) once for each face actually hit, and never for the others.
    """
    shape = local_dirs.shape[:-1]
    d = local_dirs.reshape(-1, 3)
    idx = face_of(d)
    out = np.zeros((d.shape[0], 3), dtype=np.float32)
    for k in np.unique(idx):
        m = idx == k
        fwd, right, down = FACES[int(k)]
        dk = d[m]
        den = dk @ fwd
        a, b = (dk @ right) / den, (dk @ down) / den
        img = np.asarray(get_face_fn(int(k)).convert("RGB"), dtype=np.float32)
        hh, ww = img.shape[:2]
        u = np.clip((a + 1) * 0.5 * ww - 0.5, 0, ww - 1)
        v = np.clip((b + 1) * 0.5 * hh - 0.5, 0, hh - 1)
        u0, v0 = np.floor(u).astype(int), np.floor(v).astype(int)
        u1, v1 = np.minimum(u0 + 1, ww - 1), np.minimum(v0 + 1, hh - 1)
        fu, fv = (u - u0)[:, None], (v - v0)[:, None]
        out[m] = ((img[v0, u0] * (1 - fu) + img[v0, u1] * fu) * (1 - fv)
                  + (img[v1, u0] * (1 - fu) + img[v1, u1] * fu) * fv)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8).reshape(*shape, 3)


def render_view(get_face_fn: Callable[[int], Image.Image], quat, direction_world,
                hfov_deg: float = 90, pitch_deg: float = -15, size: int = 768,
                *, supersample: int = 2) -> Image.Image:
    """Perspective view out of one panorama toward a world direction.

    get_face_fn(i) returns cube face i (0-5) as a PIL image; only faces in view are requested.
    quat is the pano's rotation [x, y, z, w]; direction_world is a world vector (Z up).
    `supersample` renders at N x size and box-averages down: with 2k faces a 768 px view
    would otherwise skip face pixels and alias (moire on carpet, jagged grout lines).
    """
    ss = max(1, int(supersample))
    rays = view_rays(direction_world, hfov_deg, pitch_deg, size * ss)
    local = rays @ world_to_local(quat).T
    img = Image.fromarray(sample_cube(get_face_fn, local))
    return img.reduce(ss) if ss > 1 else img


def directional_crop(model_id: str, src_id: str, dst_id: str, hfov_deg: float = 90,
                     pitch_deg: float = -15, size: int = 768,
                     resolution: str | None = None, *, supersample: int = 2) -> Image.Image:
    """View from sweep `src_id` toward neighbor `dst_id` (camera to camera, plus pitch).

    Faces come from matterport.get_face at `resolution` (default matterport.DEFAULT_RESOLUTION),
    fetched lazily and cached per model/sweep/resolution/face.
    """
    from . import matterport as mp

    res = resolution or mp.DEFAULT_RESOLUTION
    sweeps = mp.get_sweeps(model_id)
    a, b = sweeps[src_id], sweeps[dst_id]
    direction = np.subtract(b["pano_xyz"], a["pano_xyz"])
    faces: dict[int, Image.Image] = {}
    fetch = mp.face_fetcher(model_id, src_id, res)

    def get_face_fn(i: int) -> Image.Image:
        if i not in faces:
            faces[i] = fetch(i)
        return faces[i]

    return render_view(get_face_fn, a["quat"], direction, hfov_deg, pitch_deg, size,
                       supersample=supersample)


def _resolve(sweeps: dict, sid: str) -> str:
    if sid in sweeps:
        return sid
    hits = [k for k in sweeps if k.startswith(sid)]
    if len(hits) != 1:
        raise SystemExit(f"sweep {sid!r} matches {len(hits)} sweeps")
    return hits[0]


def main(argv=None) -> None:
    from . import matterport as mp

    ap = argparse.ArgumentParser(description="Render a directional crop from sweep A toward B.")
    ap.add_argument("--model", required=True)
    ap.add_argument("--src", required=True, help="sweep id (or unique prefix)")
    ap.add_argument("--dst", required=True, help="neighbor sweep id (or unique prefix)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hfov", type=float, default=90)
    ap.add_argument("--pitch", type=float, default=-15)
    ap.add_argument("--size", type=int, default=768)
    ap.add_argument("--res", default=mp.DEFAULT_RESOLUTION, choices=mp.RESOLUTIONS)
    a = ap.parse_args(argv)
    sweeps = mp.get_sweeps(a.model)
    src, dst = _resolve(sweeps, a.src), _resolve(sweeps, a.dst)
    img = directional_crop(a.model, src, dst, a.hfov, a.pitch, a.size, a.res)
    img.save(a.out, quality=90)
    d = np.subtract(sweeps[dst]["pano_xyz"], sweeps[src]["pano_xyz"])
    print(f"{src} -> {dst}: bearing {math.degrees(math.atan2(d[1], d[0])) % 360:.0f} deg "
          f"(world, CCW from +X), dist {math.hypot(d[0], d[1]):.2f} m, dz {d[2]:+.2f} m, "
          f"faces {faces_for_view(sweeps[src]['quat'], d, a.hfov, a.pitch)} -> {a.out}")


if __name__ == "__main__":
    main()
