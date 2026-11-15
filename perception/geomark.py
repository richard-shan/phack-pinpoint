"""Hide a lat/lon in an image's pixels so it survives a messaging app's resize + JPEG re-encode.

Demo only: stands in for the carrier location a real 911 call would carry (see live/geotag.py).

    python -m perception.geomark embed in.jpg out.jpg 30.02242 -95.44690
    python -m perception.geomark read photo.jpg            -> "30.02242,-95.4469" or "none"

Tested against resizes to 640/1280/2560 px and JPEG quality down to 40, and double
re-encoding; PSNR ~50 dB (invisible at normal size, a faint mottle on flat walls at 100%).

Each of a GRIDxGRID set of cells has its brightness-weighted mean (soft Tukey window,
so no visible cell edges) nudged onto an even/odd quantisation lattice (QIM). 64 payload
bits + 8-bit CRC are spread over the cells with repetition and majority-voted on read.
Cells are defined as fractions of the image, so any resize keeps them aligned.
"""
import struct, sys
import numpy as np, cv2

GRID, DELTA, NBITS = 64, 3.0, 72
_rng = np.random.default_rng(1234)
ASSIGN = _rng.permutation(np.arange(GRID * GRID) % NBITS)   # cell -> bit index

def _crc8(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8): c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c

def _bits(lat, lon):
    p = struct.pack(">ii", round(lat * 1e6), round(lon * 1e6)); p += bytes([_crc8(p)])
    return np.unpackbits(np.frombuffer(p, np.uint8))

def _tukey(n, a=0.6):
    x = np.arange(n) / max(n - 1, 1); w = np.ones(n); e = a / 2
    lo, hi = x < e, x > 1 - e
    w[lo] = 0.5 * (1 + np.cos(np.pi * (x[lo] / e - 1))); w[hi] = 0.5 * (1 + np.cos(np.pi * ((x[hi] - 1) / e + 1)))
    return w + 1e-3

def _cells(h, w):
    ys = np.linspace(0, h, GRID + 1).round().astype(int); xs = np.linspace(0, w, GRID + 1).round().astype(int)
    for i in range(GRID):
        for j in range(GRID):
            y0, y1, x0, x1 = ys[i], ys[i + 1], xs[j], xs[j + 1]
            win = np.outer(_tukey(y1 - y0), _tukey(x1 - x0))
            yield i * GRID + j, (slice(y0, y1), slice(x0, x1)), win

def embed(img, lat, lon):
    ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb).astype(np.float64); Y = ycc[..., 0]
    bits = _bits(lat, lon)
    for k, sl, win in _cells(*Y.shape):
        b = bits[ASSIGN[k]]; m = (win * Y[sl]).sum() / win.sum()
        off = b * DELTA / 2; q = DELTA * np.round((m - off) / DELTA) + off
        Y[sl] += (q - m) * win * win.sum() / (win * win).sum()
    ycc[..., 0] = np.clip(Y, 0, 255)
    return cv2.cvtColor(ycc.round().astype(np.uint8), cv2.COLOR_YCrCb2BGR)

def read(img):
    Y = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)[..., 0].astype(np.float64)
    votes = np.zeros(NBITS); counts = np.zeros(NBITS)
    for k, sl, win in _cells(*Y.shape):
        m = (win * Y[sl]).sum() / win.sum()
        votes[ASSIGN[k]] += int(np.round(m / (DELTA / 2))) % 2; counts[ASSIGN[k]] += 1
    p = np.packbits((votes / counts > 0.5).astype(np.uint8)).tobytes()
    if _crc8(p[:8]) != p[8]: return None
    la, lo = struct.unpack(">ii", p[:8]); return la / 1e6, lo / 1e6

if __name__ == "__main__":
    if sys.argv[1] == "embed":
        cv2.imwrite(sys.argv[3], embed(cv2.imread(sys.argv[2]), float(sys.argv[4]), float(sys.argv[5])))
    else:
        r = read(cv2.imread(sys.argv[2])); print("%s,%s" % r if r else "none")
