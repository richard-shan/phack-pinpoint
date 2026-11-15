"""Approval and real Telegram upload boundaries, without external network writes."""
import io
import json
import unittest
from unittest.mock import patch

from live.delivery import approved_packet
from live.telegram import Telegram, TelegramError
from live.tests import test_telegram as fixture


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        fixture.BridgeTests.setUp(self)

    def tearDown(self):
        fixture.BridgeTests.tearDown(self)

    def intake(self):
        self.bot.updates = [fixture.BridgeTests.update(self, 1, text='/building 6550930'),
            fixture.BridgeTests.update(self, 2, photo=[{'file_id': 'large', 'width': 100, 'height': 100}])]
        self.bridge.poll()
        return self.bridge.active(self.bridge.state['chats']['42'])['incident']

    def test_direct_document_only_once_after_approval(self):
        cid = self.intake()
        with patch.object(self.bot, 'send_packet', create=True,
                          return_value={'status': 'accepted', 'messageId': 7}) as send:
            self.bridge.relay()
            send.assert_not_called()
            self.console.update(cid, packetMarkdown='Full content ' * 1000, route='Reviewed stairs')
            inc = self.console.decide(cid, 'approved')
            self.bridge.relay()
            self.bridge.relay()
            send.assert_called_once()
            self.assertEqual(send.call_args.args[0], '42')
            self.assertEqual(send.call_args.args[1]['id'], inc['packetId'])
            self.console.update(cid, route='Unreviewed change')
            self.bridge.relay()
            send.assert_called_once()

    def test_ambiguous_bot_upload_is_not_retried(self):
        cid = self.intake()
        inc = self.console.decide(cid, 'approved')
        with patch.object(self.bot, 'send_packet', create=True, side_effect=TelegramError('timeout')) as send:
            self.bridge.relay()
            self.bridge.relay()
            send.assert_called_once()
        record = self.bridge.active(self.bridge.state['chats']['42'])
        self.assertEqual(record['packets'][inc['packetId']]['status'], 'uncertain')

    def test_edit_during_snapshot_fetch_cancels_delivery(self):
        cid = self.intake()
        inc = self.console.decide(cid, 'approved')
        original = self.console.packet
        def raced(packet_id):
            packet = original(packet_id)
            self.console.update(cid, route='New unreviewed route')
            return packet
        with patch.object(self.console, 'packet', side_effect=raced):
            self.assertIsNone(approved_packet(self.console, inc))

    def test_telegram_upload_contains_full_packet_not_local_link(self):
        cid = self.intake()
        content = 'Reviewed evidence ' * 1000
        self.console.update(cid, packetMarkdown=content)
        inc = self.console.decide(cid, 'approved')
        snapshot = self.console.packet(inc['packetId'])
        response = io.BytesIO(json.dumps({'ok': True, 'result': {'message_id': 8}}).encode())
        with patch('urllib.request.urlopen', return_value=response) as upload:
            receipt = Telegram('secret-token').send_packet('42', snapshot)
        self.assertEqual(receipt, {'status': 'accepted', 'messageId': 8})
        request = upload.call_args.args[0]
        self.assertIn(content.strip().encode(), request.data)
        self.assertIn(b'name="document"', request.data)
        self.assertNotIn(b'localhost', request.data)

    def test_telegram_error_does_not_expose_token(self):
        cid = self.intake()
        inc = self.console.decide(cid, 'approved')
        snapshot = self.console.packet(inc['packetId'])
        with patch('urllib.request.urlopen', side_effect=OSError('https://api.telegram.org/botSECRET')):
            with self.assertRaises(TelegramError) as error:
                Telegram('SECRET').send_packet('42', snapshot)
        self.assertNotIn('SECRET', str(error.exception))
