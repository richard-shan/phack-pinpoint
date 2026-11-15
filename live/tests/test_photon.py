"""Photon wire shapes against real console storage; no external messages sent."""
import copy
import io
from concurrent.futures import Future
import unittest
from pathlib import Path
from unittest.mock import patch

from live.photon import Photon, PhotonBridge, PhotonError, VERSION
from live.tests import test_telegram as telegram_tests
JPEG = telegram_tests.JPEG

PROJECT = 'pho_prj_' + '0' * 25 + '1'


def pid(kind, number):
    return 'pho_' + kind + '_' + str(number).zfill(26)


class FakePhoton:
    project = PROJECT

    def __init__(self):
        self.sent = []
        self.photos = []
        self.uploads = []

    def upload_document(self, raw, filename, key):
        self.uploads.append((raw, filename, key))
        return {'id': pid('att', 99)}

    def call(self, method, path, body=None, key=None):
        assert method == 'POST' and path == '/messages'
        self.sent.append((key, copy.deepcopy(body)))
        return {'id': pid('msg', 100 + len(self.sent)), 'status': 'dispatched'}

    def photo(self, attachment_id):
        self.photos.append(attachment_id)
        return JPEG


def incoming(number, content, handle='42'):
    user = dict(projectId=PROJECT, platform='telegram', metadata={}, details={})
    message = {'id': pid('msg', number), 'projectId': PROJECT, 'platform': 'telegram',
        'status': 'received', 'content': content,
        'sentBy': dict(user, id=pid('usr', 1), kind='human', handle=handle),
        'receivedBy': dict(user, id=pid('usr', 2), kind='agent', handle='123'),
        'space': dict(user, id=pid('spc', 1), kind='direct', participantIds=[pid('usr', 1), pid('usr', 2)])}
    return {'id': pid('evt', number), 'type': 'message.received', 'apiVersion': VERSION,
            'source': 'telegram.photon', 'data': {'projectId': PROJECT, 'object': message}}


def text(value):
    return {'type': 'text', 'format': 'plain', 'text': value}


class PhotonTests(unittest.TestCase):
    # Reuse only the console-server fixture, not the Telegram tests.
    def setUp(self):
        telegram_tests.BridgeTests.setUp(self)
        self.bridge.pool.shutdown()
        self.api = FakePhoton()
        self.bridge = PhotonBridge(self.api, self.console, self.path, allowed_handles=['42'],
                                   agent_handle='123', detector=lambda *_: {'status': 'unknown'})

    def tearDown(self):
        telegram_tests.BridgeTests.tearDown(self)

    def test_imessage_photo_reply_without_packet_delivery(self):
        from unittest.mock import Mock
        bot = Mock()
        self.bridge.telegram.packet_bot = bot
        def event(number, content):
            value = incoming(number, content)
            value['data']['object']['platform'] = 'imessage'
            for field in ('sentBy', 'receivedBy', 'space'):
                value['data']['object'][field]['platform'] = 'imessage'
            return value
        self.bridge.process(event(1, text('/building 6550930')), 1)
        self.bridge.process(event(2, {'type': 'group', 'parts': [text('Near the lift'),
            {'type': 'attachment', 'attachmentId': pid('att', 1)}]}), 2)
        self.bridge.finish_jobs()
        cid = self.bridge.active(self.bridge.state['chats'][pid('spc', 1)])['incident']
        self.assertEqual(self.console.get(cid)['callerText'], 'Near the lift')
        self.console.message(cid, 'dispatcher', 'Which floor?')
        self.bridge.relay()
        self.bridge.process(event(3, text('Second floor')), 3)
        self.assertEqual(self.console.get(cid)['messages'][-1]['text'], 'Second floor')
        self.console.decide(cid, 'approved')
        self.bridge.relay()
        self.assertFalse(any(body['content']['type'] == 'attachment' for _, body in self.api.sent))
        self.assertEqual(self.api.uploads, [])
        bot.send_packet.assert_not_called()
        count = len(self.api.sent)
        self.bridge.process(event(2, {'type': 'attachment', 'attachmentId': pid('att', 1)}), 4)
        self.bridge.relay()
        self.assertEqual(len(self.api.sent), count)
        self.assertEqual(len(self.console.incidents()), 1)

    def test_imessage_assignment_pagination_and_reuse(self):
        api = Photon('test-key', PROJECT)
        existing = {'handle': '+15555550123', 'assignedPhoneNumber': '+15555550999'}
        with patch.object(api, 'call', side_effect=[
                {'assignments': [], 'nextPageToken': 'a/b'},
                {'assignments': [existing]}]) as call:
            self.assertEqual(api.assign_imessage('+15555550123'), existing)
        self.assertEqual(call.call_count, 2)
        self.assertIn('pageToken=a%2Fb', call.call_args.args[1])

    def test_imessage_assignment_creation_is_idempotent_and_sends_no_invite(self):
        api = Photon('test-key', PROJECT)
        with patch.object(api, 'imessage_assignments', return_value=[]), patch.object(api, 'call') as call:
            api.assign_imessage('person@example.com')
            first = call.call_args
            api.assign_imessage('person@example.com')
            self.assertEqual(call.call_args, first)
            self.assertEqual(first.args[2], {'handle': 'person@example.com'})
        with self.assertRaises(ValueError):
            api.assign_imessage('5551234')

    def test_pdf_upload_has_two_parts_and_valid_stable_boundary(self):
        from email.parser import BytesParser
        from email.policy import default
        api = Photon('test-key', PROJECT)
        with patch.object(api, 'call', return_value={'id': pid('att', 99)}) as call:
            api.upload_document(b'%PDF-test', 'packet.pdf', 'stable-key')
            first = call.call_args
            api.upload_document(b'%PDF-test', 'packet.pdf', 'stable-key')
        self.assertEqual(first, call.call_args)
        raw = ('Content-Type: ' + first.kwargs['content_type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + first.args[2]
        parsed = BytesParser(policy=default).parsebytes(raw)
        self.assertLessEqual(len(parsed.get_boundary()), 70)
        parts = list(parsed.iter_parts())
        self.assertEqual(len(parts), 2)
        import json
        self.assertEqual(json.loads(parts[0].get_payload(decode=True))['sizeBytes'], 9)
        self.assertEqual(parts[1].get_payload(decode=True), b'%PDF-test')

    def photon_intake(self):
        self.bridge.process(incoming(1, text('/building 6550930')), 1)
        self.bridge.process(incoming(2, {'type': 'group', 'parts': [text('Near the lift'),
            {'type': 'attachment', 'attachmentId': pid('att', 1)}]}), 2)
        self.bridge.finish_jobs()
        return self.bridge.active(self.bridge.state['chats'][pid('spc', 1)])['incident']

    def photo_event(self, number, caption):
        return incoming(number, {'type': 'attachment', 'attachmentId': pid('att', number),
            'metadata': {'caption': {'format': 'plain', 'text': caption}}})

    def finish_detection(self, status='matched'):
        # Resolve asynchronous detection deterministically; the real console still
        # creates and persists the incidents and caller messages.
        future = Future()
        future.set_result({'status': status, 'building_id': '6550930'})
        with patch.object(self.bridge.pool, 'submit', return_value=future):
            self.bridge.finish_jobs()

    def test_commandless_photos_bind_replies_during_detection_and_after_restart(self):
        self.bridge.process(self.photo_event(1, 'First photo'), 1)
        sent = len(self.api.sent)
        self.bridge.process(incoming(2, text('Details for first')), 2)
        self.assertEqual(len(self.api.sent), sent)
        self.bridge.process(self.photo_event(3, 'Second photo'), 3)
        self.bridge.process(incoming(4, text('Details for second')), 4)
        self.assertEqual(self.console.incidents(), [])  # still identifying
        self.bridge.pool.shutdown(wait=True)
        self.bridge = PhotonBridge(self.api, self.console, self.path)
        self.finish_detection()
        for _, body in self.api.sent:
            self.assertNotIn('pho_msg_', body['content']['text'])
            self.assertNotIn('inc-', body['content']['text'])
        session = self.bridge.state['chats'][pid('spc', 1)]
        first = session['photos'][pid('msg', 1)]['incident']
        second = session['photos'][pid('msg', 3)]['incident']
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.console.incidents()), 2)
        self.assertEqual(session['active'], pid('msg', 3))
        sent = len(self.api.sent)
        self.bridge.process(incoming(5, text('More details for second')), 5)
        self.assertEqual(len(self.api.sent), sent)
        self.assertEqual([m['text'] for m in self.console.get(first)['messages']],
                         ['First photo', 'Details for first'])
        self.assertEqual([m['text'] for m in self.console.get(second)['messages']],
                         ['Second photo', 'Details for second', 'More details for second'])
        # Replaying an older photo cannot create a duplicate or switch the active case.
        self.bridge.process(self.photo_event(1, 'First photo'), 6)
        self.assertEqual(session['active'], pid('msg', 3))
        self.assertEqual(len(self.console.incidents()), 2)

    def test_commandless_unknown_photo_does_not_leak_replies_to_previous_case(self):
        self.bridge.process(self.photo_event(1, 'Known photo'), 1)
        self.finish_detection()
        session = self.bridge.state['chats'][pid('spc', 1)]
        first = self.bridge.active(session)['incident']
        self.bridge.process(self.photo_event(2, 'Unknown photo'), 2)
        self.bridge.process(incoming(3, text('While checking unknown')), 3)
        self.finish_detection('unknown')
        self.bridge.process(incoming(4, text('After unknown result')), 4)
        self.assertEqual([m['text'] for m in self.console.get(first)['messages']], ['Known photo'])
        self.assertEqual([r['text'] for r in self.bridge.active(session)['replies']],
                         ['While checking unknown', 'After unknown result'])
        self.assertNotIn('incident', self.bridge.active(session))

    def test_photon_photo_reply_approval_and_replay(self):
        cid = self.photon_intake()
        self.assertEqual(self.console.get(cid)['callerText'], 'Near the lift')
        self.assertEqual(self.api.photos, [pid('att', 1)])
        self.console.message(cid, 'dispatcher', 'Which floor?')
        self.bridge.relay()
        self.bridge.process(incoming(3, text('Second floor')), 3)
        self.assertEqual(self.console.get(cid)['messages'][-1]['text'], 'Second floor')
        self.console.update(cid, location='Second floor', route='Main stairs', packetMarkdown='Evidence ' * 1200)
        count = len(self.api.sent)
        self.bridge.relay()
        self.assertEqual(len(self.api.sent), count)  # no draft delivery
        approved = self.console.decide(cid, 'approved')
        self.bridge.relay()
        packet_calls = [body for key, body in self.api.sent if body['content']['type'] == 'attachment']
        self.assertEqual(packet_calls, [])
        self.assertEqual(self.api.uploads, [])
        count = len(self.api.sent)
        self.bridge.relay()
        restarted = PhotonBridge(self.api, self.console, self.path)
        try:
            restarted.process(incoming(3, text('Second floor')), 4)
            restarted.relay()
            self.assertEqual(len(self.api.sent), count)
            self.assertEqual(len(self.console.incidents()), 1)
            self.assertEqual([m['text'] for m in self.console.get(cid)['messages']].count('Second floor'), 1)
        finally:
            restarted.pool.shutdown()
        self.console.update(cid, route='Changed route requires new approval')
        self.bridge.relay()
        self.assertEqual(len(self.api.sent), count)

    def test_edit_during_upload_prevents_document_send(self):
        cid = self.photon_intake()
        self.console.decide(cid, 'approved')
        before = len(self.api.sent)
        def upload(*args):
            self.console.update(cid, notes='Changed during upload')
            return {'id': pid('att', 99)}
        with patch.object(self.api, 'upload_document', side_effect=upload) as uploaded:
            self.bridge.relay()
        uploaded.assert_not_called()                     # no document ever goes to the caller
        self.assertTrue(all(b['content']['type'] == 'text' for _, b in self.api.sent[before:]))

    def test_photon_rejected_and_autoapproved_never_delivered(self):
        cid = self.photon_intake()
        self.console.decide(cid, 'rejected', 'Needs review')
        count = len(self.api.sent)
        self.bridge.relay()
        self.assertEqual(len(self.api.sent), count)
        self.console.update(cid, notes='AUTO-APPROVED by the run (--auto-approve)')
        self.console.decide(cid, 'approved')
        self.bridge.relay()
        self.assertEqual(len(self.api.sent), count)

    def test_photon_isolation_and_versions(self):
        self.bridge.process(incoming(1, text('/start'), handle='43'), 1)
        self.assertEqual(self.api.sent, [])
        event = incoming(2, text('/start'))
        event['data']['object']['sentBy']['kind'] = 'agent'
        self.bridge.process(event, 2)
        self.assertEqual(self.api.sent, [])
        event = incoming(3, text('/start'))
        event['apiVersion'] = 'unknown'
        with self.assertRaises(ValueError):
            self.bridge.process(event, 3)
        self.assertEqual(self.bridge.state['cursor'], 2)

    def test_photon_failure_keeps_cursor_and_idempotency_key(self):
        event = incoming(1, text('/start'))
        with patch.object(self.api, 'call', side_effect=PhotonError('offline')) as request:
            with self.assertRaises(PhotonError):
                self.bridge.process(event, 1)
        first_key = request.call_args.kwargs['key']
        self.assertEqual(self.bridge.state['cursor'], 0)
        self.bridge.process(event, 1)
        self.assertEqual(first_key, self.api.sent[-1][0])

    def test_live_telegram_attachment_caption_metadata(self):
        self.bridge.process(incoming(1, text('/building 6550930')), 1)
        self.bridge.process(incoming(2, {'type': 'attachment', 'attachmentId': pid('att', 1),
            'metadata': {'caption': {'format': 'plain', 'text': 'Live caption near corridor'}}}), 2)
        self.bridge.finish_jobs()
        record = self.bridge.active(self.bridge.state['chats'][pid('spc', 1)])
        self.assertEqual(self.console.get(record['incident'])['callerText'], 'Live caption near corridor')

    def test_existing_telegram_bot_does_not_deliver_packet(self):
        from unittest.mock import Mock
        bot = Mock()
        bot.send_packet.return_value = {'status': 'accepted', 'messageId': 77}
        self.bridge.telegram.packet_bot = bot
        self.bridge.telegram.packet_idempotent = False
        cid = self.photon_intake()
        self.console.decide(cid, 'approved')
        count = len(self.api.sent)
        self.bridge.relay()
        bot.send_packet.assert_not_called()
        # Only a short closure text goes out, once; the packet stays with dispatch.
        (closure,) = [b for _, b in self.api.sent[count:]]
        self.assertEqual(closure['content']['type'], 'text')
        self.assertIn('Responders have your exact location', closure['content']['text'])
        self.bridge.relay()
        bot.send_packet.assert_not_called()
        self.assertEqual(len(self.api.sent), count + 1)

    def test_photon_receipt_tracks_delivery(self):
        self.bridge.process(incoming(1, text('/start')), 1)
        receipt = next(iter(self.bridge.state['outbound'].values()))
        self.assertEqual(receipt['status'], 'dispatched')
        self.bridge.process({'type': 'message.delivered', 'apiVersion': VERSION,
            'data': {'projectId': PROJECT, 'object': {'id': receipt['messageId'], 'status': 'delivered'}}}, 2)
        self.assertEqual(receipt['status'], 'delivered')
