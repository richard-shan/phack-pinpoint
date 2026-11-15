"""Telegram boundary tests with real console storage and a fake Bot API."""
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from live.console_api import Console
from live.telegram import Bridge, Telegram, TelegramError

ROOT = Path(__file__).resolve().parents[2]
JPEG = (ROOT / 'demo/6550930_3ymmrb_h020.jpg').read_bytes()


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.updates = []
        self.downloads = []

    def send(self, chat, text):
        self.sent.append((chat, text))

    def photo(self, file_id):
        self.downloads.append(file_id)
        return JPEG

    def call(self, method, **body):
        return self.updates


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        script = """
import { createLocalServer } from './app/local-server.mjs';
const server = createLocalServer({stateDirectory: process.argv[1]});
server.listen(0, '127.0.0.1', () => console.log(server.address().port));
"""
        self.server = subprocess.Popen(['node', '--input-type=module', '-e', script, self.tmp.name],
                                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        port = self.server.stdout.readline().strip()
        self.console = Console(f'http://127.0.0.1:{port}')
        self.bot = FakeTelegram()
        self.path = Path(self.tmp.name) / 'bridge.json'
        self.bridge = Bridge(self.bot, self.console, self.path,
                             detector=lambda path, buildings: {'status': 'unknown'})

    def tearDown(self):
        self.bridge.pool.shutdown(wait=True)
        self.server.terminate()
        self.server.communicate(timeout=5)
        self.tmp.cleanup()

    def update(self, number, **fields):
        return {'update_id': number, 'message': {'message_id': number,
                'chat': {'id': 42, 'type': 'private'}, **fields}}

    def intake(self):
        self.bot.updates = [self.update(1, text='/building 6550930'), self.update(2, caption='Near the lift',
            photo=[{'file_id': 'small', 'width': 10, 'height': 10},
                   {'file_id': 'large', 'width': 100, 'height': 100}])]
        self.bridge.poll()
        return self.bridge.active(self.bridge.state['chats']['42'])['incident']

    def test_photo_caption_question_reply_and_restart(self):
        cid = self.intake()
        inc = self.console.get(cid)
        self.assertEqual(inc['propertyId'], '6550930')
        self.assertEqual(inc['callerText'], 'Near the lift')
        self.assertEqual(base64.b64decode(inc['photo']['dataUrl'].split(',')[1]), JPEG)
        self.assertEqual(self.bot.downloads, ['large'])
        self.assertIn('ready for dispatcher review', self.bot.sent[-1][1])
        self.assertNotIn(cid, self.bot.sent[-1][1])
        self.console.message(cid, 'dispatcher', 'Can you see elevator doors?')
        self.bridge.relay()
        self.bridge.relay()
        self.assertEqual(sum(t.endswith('Can you see elevator doors?') for _, t in self.bot.sent), 1)
        self.bot.updates = [self.update(3, text='Yes')]
        self.bridge.poll()
        restarted = Bridge(self.bot, self.console, self.path)
        restarted.poll()
        self.assertEqual([m['text'] for m in self.console.get(cid)['messages']].count('Yes'), 1)
        self.assertEqual(len(self.console.incidents()), 1)
        self.assertEqual(restarted.state['offset'], 4)

    def test_automatic_intake_waits_and_chats_are_isolated(self):
        self.bridge.handle(self.update(1, photo=[{'file_id': 'a', 'width': 1, 'height': 1}]))
        self.assertEqual(self.console.incidents(), [])
        self.bridge.handle(self.update(2, text='/building unknown'))
        self.assertNotIn('building', self.bridge.state['chats']['42'])
        self.bridge.allowed = {'123'}
        self.bridge.handle(self.update(3, text='/building 6550930'))
        self.assertNotIn('building', self.bridge.state['chats']['42'])
        self.bridge.allowed = set()
        update = self.update(4, text='/building 6550930')
        update['message']['chat']['type'] = 'group'
        self.bridge.handle(update)
        self.assertNotIn('building', self.bridge.state['chats']['42'])

    def photo(self, number, caption=''):
        return self.update(number, caption=caption, photo=[{'file_id': 'large', 'width': 100, 'height': 100}])

    def test_every_photo_starts_incident_and_text_follows_latest(self):
        first = self.intake()
        self.bridge.handle(self.update(3, text='First follow-up'))
        self.bridge.detector = lambda *args: {'status': 'matched', 'building_id': '5956257'}
        self.bridge.handle(self.photo(4, 'Second photo'))
        self.bridge.handle(self.update(5, text='Second follow-up while detecting'))
        self.complete_detection()
        second = self.bridge.active(self.bridge.state['chats']['42'])['incident']
        self.assertNotEqual(first, second)
        self.assertEqual(self.console.get(second)['propertyId'], '5956257')
        self.assertEqual([m['text'] for m in self.console.get(first)['messages']], ['Near the lift', 'First follow-up'])
        self.assertEqual([m['text'] for m in self.console.get(second)['messages']],
                         ['Second photo', 'Second follow-up while detecting'])
        self.bridge.handle(self.update(6, text='After detection'))
        self.assertEqual(self.console.get(second)['messages'][-1]['text'], 'After detection')
        self.console.message(first, 'dispatcher', 'Older incident question')
        self.console.message(second, 'dispatcher', 'Latest incident question')
        self.bridge.relay()
        self.assertTrue(any('About an earlier photo' in t and 'Older incident question' in t for _, t in self.bot.sent))
        self.assertTrue(any(t == 'Latest incident question' for _, t in self.bot.sent))

    def test_two_pending_photos_keep_replies_separate_after_restart(self):
        self.bridge.handle(self.photo(1, 'First'))
        self.bridge.handle(self.update(2, text='For first'))
        self.bridge.handle(self.photo(3, 'Second'))
        self.bridge.handle(self.update(4, text='For second'))
        self.bridge.pool.shutdown(wait=True)
        self.bridge = Bridge(self.bot, self.console, self.path,
                             detector=lambda *args: {'status': 'matched', 'building_id': '6550930'})
        self.complete_detection()
        records = self.bridge.state['chats']['42']['photos']
        self.assertEqual(len(self.console.incidents()), 2)
        for key, expected in [('1', ['First', 'For first']), ('3', ['Second', 'For second'])]:
            self.assertEqual([m['text'] for m in self.console.get(records[key]['incident'])['messages']], expected)
        self.assertEqual(self.bridge.state['chats']['42']['active'], '3')

    def test_unknown_photo_never_routes_text_to_previous_incident(self):
        first = self.intake()
        self.bridge.handle(self.photo(3))
        self.bridge.handle(self.update(4, text='Unknown photo follow-up'))
        self.complete_detection()
        self.bridge.handle(self.update(5, text='Another unknown follow-up'))
        self.assertEqual([m['text'] for m in self.console.get(first)['messages']], ['Near the lift'])
        record = self.bridge.active(self.bridge.state['chats']['42'])
        self.assertEqual([r['text'] for r in record['replies']],
                         ['Unknown photo follow-up', 'Another unknown follow-up'])
        self.assertNotIn('incident', record)

    def test_legacy_state_preserves_incident_and_reply_deduplication(self):
        first = self.intake()
        self.console.message(first, 'caller', 'Already stored')
        self.bridge.pool.shutdown(wait=True)
        self.path.write_text(json.dumps({'offset': 3, 'chats': {'42': {
            'incident': first, 'sent': [], 'last_reply': 3}}}))
        self.bridge = Bridge(self.bot, self.console, self.path,
                             detector=lambda *args: {'status': 'unknown'})
        self.bridge.handle(self.update(3, text='Already stored'))
        self.bridge.handle(self.update(4, text='New text'))
        self.assertEqual([m['text'] for m in self.console.get(first)['messages']],
                         ['Near the lift', 'Already stored', 'New text'])

    def test_commands_do_not_change_text_target(self):
        first = self.intake()
        self.bridge.handle(self.update(3, text='/building 5956257'))
        self.bridge.handle(self.update(4, text='Still for first'))
        self.bridge.handle(self.update(5, text='/new'))
        self.bridge.handle(self.update(6, text='Also for first'))
        self.assertEqual([m['text'] for m in self.console.get(first)['messages']],
                         ['Near the lift', 'Still for first', 'Also for first'])
        self.assertNotIn('building', self.bridge.state['chats']['42'])

    def test_photo_acknowledgement_retry_does_not_create_duplicate(self):
        self.bridge.handle(self.update(1, text='/building 6550930'))
        with patch.object(self.bot, 'send', side_effect=TelegramError('offline')):
            with self.assertRaises(TelegramError):
                self.bridge.handle(self.photo(2))
        self.bridge.handle(self.photo(2))
        self.complete_detection()
        self.assertEqual(len(self.console.incidents()), 1)

    def complete_detection(self):
        self.bridge.finish_jobs()
        for future in self.bridge.jobs.values():
            future.result(timeout=5)
        self.bridge.finish_jobs()

    def test_photo_without_building_creates_matched_incident(self):
        self.bridge.detector = lambda path, buildings: {'status': 'matched', 'building_id': '6550930'}
        self.bridge.handle(self.update(1, caption='Automatic intake',
            photo=[{'file_id': 'large', 'width': 100, 'height': 100}]))
        self.complete_detection()
        cid = self.bridge.active(self.bridge.state['chats']['42'])['incident']
        self.assertEqual(self.console.get(cid)['callerText'], 'Automatic intake')
        self.assertEqual(self.console.get(cid)['propertyId'], '6550930')

    def test_detection_does_not_block_message_relay(self):
        import threading
        release = threading.Event()
        def slow_detector(*args):
            release.wait(5)
            return {'status': 'unknown'}
        self.bridge.detector = slow_detector
        cid = self.intake()
        other = self.update(3, photo=[{'file_id': 'large', 'width': 100, 'height': 100}])
        other['message']['chat']['id'] = 43
        try:
            self.bridge.handle(other)
            self.bridge.finish_jobs()
            self.console.message(cid, 'dispatcher', 'A question while another photo is processing')
            self.bridge.relay()
            self.assertTrue(self.bot.sent[-1][1].endswith('A question while another photo is processing'))
            self.assertIn('pending', self.bridge.active(self.bridge.state['chats']['43']))
        finally:
            release.set()
            self.complete_detection()

    def test_pending_photo_survives_restart(self):
        self.bridge.handle(self.update(1, photo=[{'file_id': 'large', 'width': 100, 'height': 100}]))
        self.bridge.pool.shutdown(wait=True)
        self.bridge = Bridge(self.bot, self.console, self.path,
                             detector=lambda *args: {'status': 'matched', 'building_id': '6550930'})
        self.complete_detection()
        self.assertEqual(len(self.console.incidents()), 1)

    def test_result_send_retry_does_not_duplicate_incident(self):
        self.bridge.detector = lambda *args: {'status': 'matched', 'building_id': '6550930'}
        self.bridge.handle(self.update(1, photo=[{'file_id': 'large', 'width': 100, 'height': 100}]))
        with patch.object(self.bot, 'send', side_effect=TelegramError('offline')):
            with self.assertRaises(TelegramError):
                self.complete_detection()
        self.bridge.finish_jobs()
        self.assertEqual(len(self.console.incidents()), 1)
        self.assertNotIn('pending', self.bridge.active(self.bridge.state['chats']['42']))

    def test_unknown_creates_no_incident(self):
        self.bridge.handle(self.update(1, photo=[{'file_id': 'a', 'width': 100, 'height': 100}]))
        self.complete_detection()
        self.assertEqual(self.console.incidents(), [])
        self.assertIn('couldn’t reliably identify', self.bot.sent[-1][1])
        self.assertNotIn('pending', self.bridge.active(self.bridge.state['chats']['42']))

    def test_detection_failure_is_not_reported_as_unknown(self):
        def unavailable(*args):
            raise RuntimeError('model missing')
        self.bridge.detector = unavailable
        self.bridge.handle(self.update(1, photo=[{'file_id': 'a', 'width': 100, 'height': 100}]))
        self.bridge.finish_jobs()
        import concurrent.futures
        concurrent.futures.wait(list(self.bridge.jobs.values()))
        self.bridge.finish_jobs()
        self.assertEqual(self.console.incidents(), [])
        self.assertIn('unavailable', self.bot.sent[-1][1])

    def test_caller_reply_is_saved_silently_and_replay_does_not_duplicate(self):
        cid = self.intake()
        self.bot.updates = [self.update(3, text='Yes')]
        with patch.object(self.bot, 'send', side_effect=TelegramError('offline')) as send:
            self.bridge.poll()
            send.assert_not_called()
        self.bridge.poll()
        self.assertEqual([m['text'] for m in self.console.get(cid)['messages']].count('Yes'), 1)


class TransportTests(unittest.TestCase):
    def test_error_does_not_disclose_token(self):
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('secret-token')):
            with self.assertRaises(TelegramError) as caught:
                Telegram('secret-token').call('getMe')
        self.assertNotIn('secret-token', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
