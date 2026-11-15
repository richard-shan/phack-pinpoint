"""Demo stand-in for a carrier location ping: the caller's location rides inside the photo.

A real 911 call comes with the phone's network location. A photo sent over iMessage or
Telegram has its EXIF stripped or rewritten on the way, so for the demo the location is
hidden in the pixels instead (perception/geomark.py), where it survives the messaging
app's resize and JPEG re-encode. The bridge reads it back, picks the nearest building
that has a graph, and starts the incident with no manual choice. This is SIMULATED
location for a demo; nothing here reads a real phone's position.

Tag demo photos before the demo:

    python -m live.geotag tag photo.jpg --building 6550930          # that building's location
    python -m live.geotag tag photo.jpg --address "7900 Fannin"     # match a building address
    python -m live.geotag tag photo.jpg --latlon 29.7069,-95.4011   # explicit coordinates
    python -m live.geotag read demo-tagged/photo.jpg                # check a tagged photo
    python -m live.geotag buildings                                 # what can be tagged
    python -m live.geotag text demo-tagged/photo.jpg "I fell"       # rehearse: fake an incoming text

Tagged copies go to demo-tagged/ (or --out). AirDrop them to the phone and send them
in Messages as normal photos.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILDINGS = ROOT / 'buildings'
MAX_DISTANCE_M = 400        # farther than this from every routable building -> no auto-pick


# Where a graph's address line is too vague to read out to a caller.
ADDRESS_OVERRIDES = {'princeton-rabinowitz': 'Rabinowitz Hall, Princeton University, Princeton, NJ'}


def _short(address: str) -> str:
    """'7900 Fannin St, Houston, TX, 77054, US' -> '7900 Fannin St, Houston, TX'."""
    import re
    address = address[:-4] if address.endswith(', US') else address
    return re.sub(r',\s*\d{5}(?:-\d{4})?$', '', address)


def building_locations(buildings=None) -> dict:
    """{building_id: {"lat", "lon", "address", "name"}} from each building's aerial
    bounding box centre (every building with a graph has one, including non-CoStar ones)."""
    out = {}
    for folder in sorted(BUILDINGS.iterdir()):
        aerial, graph = folder / 'aerial.json', folder / 'graph.json'
        if not (aerial.exists() and graph.exists()):
            continue
        if buildings is not None and folder.name not in buildings:
            continue
        w, s, e, n = json.loads(aerial.read_text())['bbox_wsen']
        g = json.loads(graph.read_text())
        out[folder.name] = {'lat': round((s + n) / 2, 6), 'lon': round((w + e) / 2, 6),
                            'address': ADDRESS_OVERRIDES.get(folder.name) or _short(g.get('address') or ''), 'name': g.get('name') or folder.name}
    return out


def _distance_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371000 * 2 * math.asin(math.sqrt(a))


def nearest_building(lat: float, lon: float, buildings=None):
    """The routable building closest to (lat, lon), or None if none is within MAX_DISTANCE_M."""
    best = None
    for bid, loc in building_locations(buildings).items():
        d = _distance_m(lat, lon, loc['lat'], loc['lon'])
        if best is None or d < best['distanceM']:
            best = dict(loc, building_id=bid, distanceM=round(d, 1))
    return best if best and best['distanceM'] <= MAX_DISTANCE_M else None


def locate_photo(path, buildings=None):
    """A tagged photo -> {"building_id", "latitude", "longitude", "address", "distanceM",
    "source"}; None when the photo carries no tag or is not near a routable building."""
    import cv2
    from perception.geomark import read
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        from PIL import Image, ImageOps            # HEIC and other formats cv2 cannot open
        import numpy as np
        try:
            with Image.open(path) as im:
                img = cv2.cvtColor(np.asarray(ImageOps.exif_transpose(im).convert('RGB')), cv2.COLOR_RGB2BGR)
        except Exception:
            return None
    h, w = img.shape[:2]
    if max(h, w) > 1600:                            # the tag is resize-proof; reading small is faster
        s = 1600 / max(h, w)
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    coords = read(img)
    if coords is None:
        return None
    near = nearest_building(*coords, buildings=buildings)
    if near is None:
        return None
    return {'building_id': near['building_id'], 'latitude': coords[0], 'longitude': coords[1],
            'address': near['address'], 'distanceM': near['distanceM'], 'source': 'simulated-device'}


def _resolve(args) -> tuple[float, float, str]:
    locs = building_locations()
    if args.building:
        if args.building not in locs:
            raise SystemExit(f'Unknown building {args.building}. Choose from: {", ".join(locs)}')
        loc = locs[args.building]
        return loc['lat'], loc['lon'], loc['address']
    if args.address:
        want = args.address.lower().replace(',', ' ').split()
        hits = [(bid, loc) for bid, loc in locs.items()
                if all(word in loc['address'].lower() or word in loc['name'].lower() for word in want)]
        if len(hits) != 1:
            raise SystemExit(f'"{args.address}" matched {len(hits)} buildings; use --building. '
                             'See: python -m live.geotag buildings')
        return hits[0][1]['lat'], hits[0][1]['lon'], hits[0][1]['address']
    lat, lon = (float(v) for v in args.latlon.split(','))
    near = nearest_building(lat, lon)
    if near is None:
        print(f'warning: {lat},{lon} is more than {MAX_DISTANCE_M} m from every routable building; '
              'the bridge will fall back to photo matching.')
    return lat, lon, near['address'] if near else ''


def _rehearse(a):
    """Run one photo through the real bridge code with a stand-in for iMessage."""
    import tempfile, time
    from .console_api import Console
    from .telegram import Bridge

    class Phone:
        sent = []
        def send(self, chat, text):
            self.sent.append(text)
            print('\n--- reply on the caller\'s phone ---\n' + text + '\n')
        def photo(self, file_id):
            return a.photo.read_bytes()

    console = Console(a.console)
    if not console.alive():
        raise SystemExit(f'No console at {a.console}. Start it first: .venv/bin/python -m live.serve')

    class Rehearsal(Bridge):
        def channel(self, chat, result):
            return dict(super().channel(chat, result), platform='imessage', handle=a.handle)

    with tempfile.TemporaryDirectory() as tmp:
        bridge = Rehearsal(Phone(), console, Path(tmp) / 'state.json',
                           detector=lambda path, buildings: {'status': 'unknown', 'reason': 'not_tagged'})
        n = int(time.time())
        bridge.handle({'message': {'message_id': n, 'chat': {'id': 'rehearsal', 'type': 'private'},
                                   'caption': a.message, 'photo': [{'file_id': 'x', 'width': 1, 'height': 1}]}})
        while bridge.state['chats']['rehearsal']['photos'][str(n)].get('pending'):
            bridge.finish_jobs()
            time.sleep(0.2)
        bridge.pool.shutdown(wait=True)
        record = bridge.state['chats']['rehearsal']['photos'][str(n)]
    if record.get('incident'):
        print(f'Incident created: {a.console}/live.html#incident/{record["incident"]}')
    else:
        print('No incident: the photo has no location tag (tag it first with: python -m live.geotag tag ...)')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('tag', help='hide a location in one or more photos')
    t.add_argument('photos', nargs='+', type=Path)
    where = t.add_mutually_exclusive_group(required=True)
    where.add_argument('--building')
    where.add_argument('--address')
    where.add_argument('--latlon', help='LAT,LON')
    t.add_argument('--out', type=Path, default=ROOT / 'demo-tagged')
    r = sub.add_parser('read', help='read the location back from photos')
    r.add_argument('photos', nargs='+', type=Path)
    sub.add_parser('buildings', help='list the buildings a location can resolve to')
    x = sub.add_parser('text', help='rehearse without a phone: send a photo to the running console '
                                    'as if it were texted in, and print the reply the caller would get')
    x.add_argument('photo', type=Path)
    x.add_argument('message', nargs='?', default='')
    x.add_argument('--from', dest='handle', default='+1 (555) 010-0199')
    x.add_argument('--console', default='http://localhost:4175')
    a = ap.parse_args(argv)

    if a.cmd == 'buildings':
        for bid, loc in building_locations().items():
            print(f'{bid:22s} {loc["lat"]:.6f},{loc["lon"]:.6f}  {loc["address"]}')
        return
    if a.cmd == 'text':
        return _rehearse(a)
    import cv2
    from perception.geomark import embed
    if a.cmd == 'read':
        for p in a.photos:
            got = locate_photo(p)
            print(f'{p}: ' + (f'{got["latitude"]},{got["longitude"]} -> building {got["building_id"]} '
                              f'({got["address"]}, {got["distanceM"]:.0f} m)' if got else 'no location tag'))
        return
    lat, lon, address = _resolve(a)
    a.out.mkdir(parents=True, exist_ok=True)
    for p in a.photos:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f'Could not read {p}; convert it to JPEG or PNG first.')
        dest = a.out / (p.stem + '.jpg')
        cv2.imwrite(str(dest), embed(img, lat, lon), [cv2.IMWRITE_JPEG_QUALITY, 95])
        check = locate_photo(dest)
        ok = check and abs(check['latitude'] - lat) < 1e-5 and abs(check['longitude'] - lon) < 1e-5
        print(f'{dest}: {lat},{lon} {address}  ' + ('[verified]' if ok else '[TAG DID NOT READ BACK]'))


if __name__ == '__main__':
    main()
