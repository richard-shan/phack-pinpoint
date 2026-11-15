"""Demo location tag: survives messaging-app recompression and starts an incident with no manual choice."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import cv2
import numpy as np

from live import geotag
from live.console_api import Console
from live.telegram import Bridge
from perception import geomark

ROOT = Path(__file__).resolve().parents[2]
PHOTO = ROOT / 'demo/6550930_3ymmrb.jpg'


def recompress(img, cap=1280, quality=75):
    """What iMessage/Telegram do to a photo: shrink it and re-encode as JPEG (metadata gone)."""
    h, w = img.shape[:2]
    s = min(1, cap / max(h, w))
    if s < 1:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    return cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


class GeomarkTests(unittest.TestCase):
    def test_round_trip_through_recompression(self):
        img = cv2.imread(str(PHOTO))
        marked = geomark.embed(img, 29.706912, -95.401123)
        self.assertGreater(cv2.PSNR(img, marked), 45)
        for cap, quality in [(1280, 85), (1280, 50), (2560, 70), (640, 60)]:
            got = geomark.read(cv2.imdecode(np.frombuffer(recompress(marked, cap, quality), np.uint8), 1))
            self.assertEqual(got, (29.706912, -95.401123), (cap, quality))

    def test_untagged_photo_reads_nothing(self):
        self.assertIsNone(geomark.read(cv2.imread(str(PHOTO))))

    def test_nearest_building(self):
        loc = geotag.building_locations()['6550930']
        self.assertEqual(geotag.nearest_building(loc['lat'], loc['lon'])['building_id'], '6550930')
        self.assertIsNone(geotag.nearest_building(0.0, 0.0))


class FakeChannel:
    def __init__(self, photo):
        self.sent, self.updates, self.raw = [], [], photo

    def send(self, chat, text):
        self.sent.append((chat, text))

    def photo(self, file_id):
        return self.raw

    def call(self, method, **body):
        return self.updates


class TaggedIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        script = """
import { createLocalServer } from './app/local-server.mjs';
const server = createLocalServer({stateDirectory: process.argv[1]});
server.listen(0, '127.0.0.1', () => console.log(server.address().port));
"""
        self.server = subprocess.Popen(['node', '--input-type=module', '-e', script, self.tmp.name],
                                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.console = Console(f'http://127.0.0.1:{self.server.stdout.readline().strip()}')

    def tearDown(self):
        self.bridge.pool.shutdown(wait=True)
        self.server.terminate()
        self.server.communicate(timeout=5)
        self.tmp.cleanup()

    def make_bridge(self, raw, detector):
        self.bot = FakeChannel(raw)
        self.bridge = Bridge(self.bot, self.console, Path(self.tmp.name) / 'bridge.json', detector=detector)

    def photo_update(self, number, caption=''):
        return {'update_id': number, 'message': {'message_id': number, 'chat': {'id': 7, 'type': 'private'},
                'caption': caption, 'photo': [{'file_id': 'f', 'width': 1, 'height': 1}]}}

    def test_tagged_photo_creates_incident_without_choice_or_detector(self):
        loc = geotag.building_locations()['6550930']
        tagged = recompress(geomark.embed(cv2.imread(str(PHOTO)), loc['lat'], loc['lon']))

        def detector(path, buildings):
            raise AssertionError('a tagged photo must not wait for photo matching')
        self.make_bridge(tagged, detector)
        self.bot.updates = [self.photo_update(1, 'I fell, I can’t get up')]
        self.bridge.poll()
        record = self.bridge.active(self.bridge.state['chats']['7'])
        inc = self.console.get(record['incident'])
        self.assertEqual(inc['propertyId'], '6550930')
        self.assertEqual(inc['channel']['platform'], 'telegram')
        self.assertAlmostEqual(inc['channel']['location']['latitude'], loc['lat'], places=5)
        self.assertEqual(inc['channel']['location']['address'], '7900 Fannin St, Houston, TX')
        # One kind reply, sent once, and recorded in the console thread as an automatic reply.
        replies = [t for _, t in self.bot.sent]
        self.assertEqual(len(replies), 1)
        self.assertIn('Help is on the way', replies[0])
        self.assertIn('7900 Fannin', replies[0])
        self.assertEqual([m['role'] for m in inc['messages']], ['caller', 'auto'])
        # Follow-up text joins the same incident; relaying does not resend the automatic reply.
        self.bot.updates = [{'update_id': 2, 'message': {'message_id': 2, 'chat': {'id': 7, 'type': 'private'},
                                                         'text': 'Two of us, near the lobby desk'}}]
        self.bridge.poll()
        self.bridge.relay()
        inc = self.console.get(record['incident'])
        self.assertEqual(inc['messages'][-1]['text'], 'Two of us, near the lobby desk')
        self.assertEqual(len(self.bot.sent), 1)
        self.assertEqual(self.console.incidents()[0]['channel']['location']['address'],
                         inc['channel']['location']['address'])

    def test_untagged_photo_still_uses_photo_matching(self):
        calls = []

        def detector(path, buildings):
            calls.append(path)
            return {'status': 'unknown', 'reason': 'weak_evidence'}
        self.make_bridge(PHOTO.read_bytes(), detector)
        self.bot.updates = [self.photo_update(1)]
        self.bridge.poll()
        while not calls or self.bridge.jobs:
            self.bridge.finish_jobs()
        self.assertEqual(len(calls), 1)
        self.assertNotIn('Help is on the way', ' '.join(t for _, t in self.bot.sent))


if __name__ == '__main__':
    unittest.main()
