"""Cut ordinary camera views out of a Matterport panorama.

A panorama arrives as six square cube faces, 0.jpg to 5.jpg. Their layout was
worked out by matching pixels along face edges (unanimous over nine panoramas):

    0 up (face 1 is at the bottom of the image)     1 front   2 right
    5 down (face 1 is at the top of the image)      3 back    4 left

Turning right from face 1 you see 2, then 3, then 4. Yaw here is degrees to the
right of face 1's centre; it is relative to the scan, not to north.
"""
import cv2
import numpy as np

# Axes: x right, y up, z forward (into face 1). Per face: (forward, right, up).
FACES = {
    0: ((0, 1, 0), (1, 0, 0), (0, 0, -1)),
    1: ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    2: ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    3: ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
    4: ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    5: ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
}


def load_faces(paths):
    """Six face images as BGR arrays, in file order 0..5. The up and down faces (0, 5)
    may be missing (bulk downloads of lower-priority scans skip them to save image
    requests); they are filled with flat grey, so steeply tilted views lose ceiling or
    floor detail. The four side faces are required."""
    faces = [cv2.imread(p) if p else None for p in paths]
    if len(faces) != 6 or any(faces[i] is None for i in (1, 2, 3, 4)):
        raise ValueError("need the four side cube faces, got %r" % (paths,))
    for i in (0, 5):
        if faces[i] is None:
            faces[i] = cv2.blur(faces[1], (1, 1)) * 0 + 128
    return faces


def view(faces, yaw_deg, pitch_deg=0.0, fov_deg=75.0, size=448, roll_deg=0.0):
    """A square pinhole-camera view. fov_deg is the horizontal field of view."""
    half = np.tan(np.radians(fov_deg) / 2)
    u, v = np.meshgrid(np.linspace(-half, half, size), np.linspace(half, -half, size))
    d = np.stack([u, v, np.ones_like(u)], -1)                       # camera rays
    r, p, y = np.radians(roll_deg), np.radians(pitch_deg), np.radians(yaw_deg)
    roll = np.array([[np.cos(r), -np.sin(r), 0], [np.sin(r), np.cos(r), 0], [0, 0, 1]])
    pitch = np.array([[1, 0, 0], [0, np.cos(p), np.sin(p)], [0, -np.sin(p), np.cos(p)]])
    yaw = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    d = d @ (yaw @ pitch @ roll).T                                  # into panorama axes
    out = np.zeros((size, size, 3), np.uint8)
    best = np.argmax(np.stack([d @ np.array(FACES[i][0], float) for i in range(6)], -1), -1)
    for i, (f, rt, up) in FACES.items():
        mask = best == i
        if not mask.any():
            continue
        n = faces[i].shape[0]
        depth = d @ np.array(f, float)
        depth = np.where(mask, depth, 1.0)
        fx = (d @ np.array(rt, float)) / depth                      # -1..1 across the face
        fy = (d @ np.array(up, float)) / depth
        mx = ((fx + 1) / 2 * (n - 1)).astype(np.float32)
        my = ((1 - fy) / 2 * (n - 1)).astype(np.float32)
        part = cv2.remap(faces[i], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        out[mask] = part[mask]
    return out
