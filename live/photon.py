"""Photon 2.0 caller transport using project REST APIs and resumable Socket.IO events."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from .console_api import Console, ConsoleError
from .telegram import Bridge, InvalidPhoto, MAX_DOWNLOAD, Telegram, TelegramError, WELCOME

ORIGIN = 'https://api.photon.codes'
STATE = Path(__file__).resolve().parent.parent / 'cache' / 'photon.json'
VERSION = '2026-07-01'


class PhotonError(TelegramError):
    def __init__(self, message, retry_after=5):
        super().__init__(message)
        self.retry_after = retry_after


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # Never forward a project credential to a different origin.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Photon:
    def __init__(self, key, project):
        if not re.fullmatch(r'pho_prj_[0-7][0-9a-hjkmnp-tv-z]{25}', project):
            raise ValueError('Invalid PHOTON_PROJECT_ID')
        self.key, self.project = key, project
        self.opener = urllib.request.build_opener(NoRedirect)

    def call(self, method, suffix, body=None, key=None, binary=False, content_type=None):
        headers = {'Authorization': f'Bearer {self.key}', 'User-Agent': 'Pinpoint/1.0'}
        if body is not None:
            headers['Content-Type'] = content_type or 'application/json'
        if key:
            headers['Idempotency-Key'] = key
        req = urllib.request.Request(f'{ORIGIN}/v1/projects/{self.project}{suffix}',
            data=(body if isinstance(body, bytes) else json.dumps(body).encode()) if body is not None else None,
            method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=30) as response:
                raw = response.read((MAX_DOWNLOAD if binary else 4 * 1024 * 1024) + 1)
                if binary:
                    if len(raw) > MAX_DOWNLOAD:
                        raise InvalidPhoto('Photo is too large')
                    return raw
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            delay = exc.headers.get('Retry-After', '5')
            code = exc.code
            exc.close()
            raise PhotonError(f'Photon HTTP {code}; check project permissions or API Activities',
                              int(delay) if delay.isdigit() else 5) from None
        except (OSError, ValueError):
            raise PhotonError('Photon request failed; check connectivity and configuration') from None

    def upload_document(self, raw, filename, key):
        boundary = 'pinpoint-' + hashlib.sha256(key.encode()).hexdigest()[:40]
        metadata = json.dumps({'contentType': 'application/pdf', 'sizeBytes': len(raw), 'filename': filename})
        body = (f'--{boundary}\r\nContent-Type: application/json\r\n\r\n{metadata}\r\n'
                f'--{boundary}\r\nContent-Type: application/pdf\r\n\r\n').encode()
        body += raw + f'\r\n--{boundary}--\r\n'.encode()
        return self.call('POST', '/attachments', body, key=key,
                         content_type=f'multipart/related; boundary={boundary}')

    def photo(self, attachment_id):
        if not re.fullmatch(r'pho_att_[0-7][0-9a-hjkmnp-tv-z]{25}', attachment_id):
            raise InvalidPhoto('Invalid attachment ID')
        info = self.call('GET', f'/attachments/{attachment_id}')
        if info.get('projectId') != self.project or not info.get('contentType', '').startswith('image/'):
            raise InvalidPhoto('Send an image attachment')
        if info.get('sizeBytes', MAX_DOWNLOAD + 1) > MAX_DOWNLOAD:
            raise InvalidPhoto('Photo is too large')
        raw = self.call('GET', f'/attachments/{attachment_id}/content', binary=True)
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 40_000_000:
                    raise InvalidPhoto('Image dimensions are too large')
                image.verify()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise InvalidPhoto('Image could not be decoded') from None
        return raw


class PhotonTransport:
    packet_idempotent = True

    def __init__(self, api, packet_bot=None):
        self.api = api
        self.packet_bot = packet_bot
        self.packet_idempotent = packet_bot is None
        self.bridge = None

    def photo(self, attachment_id):
        return self.api.photo(attachment_id)

    def send_once(self, chat, text, key):
        bridge = self.bridge
        if text == WELCOME and self.packet_bot is not None:
            text += ('\n\nApproved packets arrive from our packet bot. Start it once before your demo: '
                     f'https://t.me/{self.packet_bot.username}')
        route = bridge.state['routes'][str(chat)]
        identity = hashlib.sha256(f'{chat}:{key}'.encode()).hexdigest()
        receipts = bridge.state['outbound']
        if identity in receipts:
            return receipts[identity]
        message = self.api.call('POST', '/messages',
            {'spaceId': route['spaceId'], 'senderId': route['senderId'],
             'content': {'type': 'text', 'format': 'plain', 'text': text}},
            key='pinpoint:' + identity)
        # A 202 means dispatch was accepted, not that a person received it.
        receipt = {'messageId': message['id'], 'status': message['status']}
        receipts[identity] = receipt
        bridge.save()
        return receipt

    def send_packet(self, chat, snapshot):
        if self.packet_bot is not None:
            route = self.bridge.state['routes'][str(chat)]
            if route['platform'] != 'telegram' or not route['callerHandle'].isdigit():
                raise TelegramError('The configured Telegram packet bot needs a Telegram caller')
            return self.packet_bot.send_packet(route['callerHandle'], snapshot)
        from .packet_document import packet_pdf
        bridge = self.bridge
        route = bridge.state['routes'][str(chat)]
        key = 'packet-pdf:' + snapshot['id']
        identity = hashlib.sha256(f'{chat}:{key}'.encode()).hexdigest()
        if identity in bridge.state['outbound']:
            return bridge.state['outbound'][identity]
        uploads = bridge.state.setdefault('packetUploads', {})
        if identity not in uploads:
            attachment = self.api.upload_document(packet_pdf(snapshot), 'Pinpoint-location-and-route.pdf',
                                                   'pinpoint-upload:' + identity)
            uploads[identity] = attachment['id']
            bridge.save()
        # Uploading can take time: re-check the exact approval before sending.
        from .delivery import approved_packet
        current = approved_packet(bridge.console, bridge.console.get(snapshot['incident']['id']))
        if current is None or current['id'] != snapshot['id']:
            raise PhotonError('Packet approval changed before document delivery')
        caption = 'Here’s your location and route, reviewed by the dispatcher.'
        body = {'spaceId': route['spaceId'], 'senderId': route['senderId'],
                'content': {'type': 'attachment', 'attachmentId': uploads[identity]}}
        if route['platform'] == 'telegram':
            body['content']['metadata'] = {'caption': {'format': 'plain', 'text': caption}}
            body['telegram'] = {'sendAs': 'file'}
        message = self.api.call('POST', '/messages', body, key='pinpoint:' + identity)
        receipt = {'messageId': message['id'], 'status': message['status'],
                   'attachmentId': uploads[identity], 'format': 'pdf'}
        bridge.state['outbound'][identity] = receipt
        bridge.save()
        return receipt


class PhotonBridge(Bridge):
    def __init__(self, api, console, state_path=STATE, allowed_handles=(), agent_handle='', detector=None,
                 packet_bot=None):
        transport = PhotonTransport(api, packet_bot)
        super().__init__(transport, console, state_path, detector=detector)
        transport.bridge = self
        self.api = api
        self.allowed_handles = set(allowed_handles)
        self.agent_handle = agent_handle
        if self.state.get('projectId', api.project) != api.project:
            raise ValueError('Use a separate Photon state file for each project')
        self.state.update(projectId=api.project)
        for key, default in [('cursor', 0), ('routes', {}), ('outbound', {}), ('processed', [])]:
            self.state.setdefault(key, default)

    def process(self, event, seq):
        if not isinstance(seq, int) or seq < 1:
            raise ValueError('Invalid Photon sequence')
        if seq <= self.state['cursor']:
            return
        if event.get('apiVersion') != VERSION:
            raise ValueError('Unsupported Photon payload version; cursor was not advanced')
        if event.get('data', {}).get('projectId') != self.api.project:
            raise ValueError('Photon event is for another project')
        if event.get('type') == 'message.received':
            self.receive(event['data']['object'])
        elif event.get('type') in ('message.accepted', 'message.delivered', 'message.failed'):
            message = event['data']['object']
            for receipt in self.state['outbound'].values():
                if receipt['messageId'] == message['id']:
                    receipt['status'] = message['status']
        self.state['cursor'] = seq
        self.save()

    def receive(self, message):
        sender, agent, space = (message.get(k) or {} for k in ('sentBy', 'receivedBy', 'space'))
        if (message.get('projectId') != self.api.project or message.get('status') != 'received'
                or message.get('platform') not in ('telegram', 'imessage')
                or sender.get('kind') != 'human' or agent.get('kind') != 'agent'
                or space.get('kind') != 'direct'):
            return
        if any(v.get('projectId') != self.api.project for v in (sender, agent, space)):
            return
        if self.agent_handle and agent.get('handle') != self.agent_handle:
            return
        if self.allowed_handles and sender.get('handle') not in self.allowed_handles:
            return
        mid = message['id']
        if mid in self.state['processed']:
            return
        if not re.fullmatch(r'pho_msg_[0-7][0-9a-hjkmnp-tv-z]{25}', mid):
            raise ValueError('Invalid Photon message ID')
        chat = space['id']
        if not re.fullmatch(r'pho_spc_[0-7][0-9a-hjkmnp-tv-z]{25}', chat):
            raise ValueError('Invalid Photon space ID')
        self.state['routes'][chat] = {'spaceId': chat, 'senderId': agent['id'],
                                     'callerHandle': sender['handle'], 'platform': message['platform']}
        self.save()
        content = message.get('content') or {}
        parts = content.get('parts', []) if content.get('type') == 'group' else [content]
        text = '\n'.join(p['text'] for p in parts if p.get('type') == 'text')
        attachments = [p['attachmentId'] for p in parts if p.get('type') == 'attachment']
        if not attachments and not text:
            return
        for index, attachment in enumerate(attachments or [None]):
            # Album attachments each start an incident, consistent with Telegram photo intake.
            key = mid + (f'-{index}' if index else '')
            incoming = {'message_id': key, 'chat': {'id': chat, 'type': 'private'}}
            if attachment:
                part = next(p for p in parts if p.get('attachmentId') == attachment)
                caption = (part.get('metadata') or {}).get('caption') or {}
                caption_text = caption.get('text', '') if isinstance(caption, dict) else ''
                combined = '\n'.join(dict.fromkeys(t for t in (text, caption_text) if t))
                incoming.update(caption=combined, photo=[{'file_id': attachment, 'width': 1, 'height': 1}])
            else:
                incoming['text'] = text
            try:
                self.handle({'message': incoming})
            except InvalidPhoto:
                self.send(chat, 'Could not read that photo. Send a JPEG, PNG, or WebP image under 20 MB.',
                          f'invalid:{key}')
        self.state['processed'].append(mid)
        self.save()


async def run_stream(bridge):
    import socketio
    sio = socketio.AsyncClient()
    queue = asyncio.Queue(maxsize=128)
    fatal = asyncio.Event()

    @sio.event
    async def connect():
        try:
            result = await sio.call('subscribe',
                {'startSeq': bridge.state['cursor'], 'apiVersion': VERSION}, timeout=10)
            if not result.get('ok'):
                raise PhotonError('Photon subscription rejected')
            if bridge.state['cursor'] == 0 and isinstance(result.get('cursor'), int):
                bridge.state['cursor'] = result['cursor']
                bridge.save()
            print('Photon 2.0 connected; waiting for caller messages.', flush=True)
        except Exception:
            fatal.set()

    @sio.on('delivery')
    def delivery(event, metadata):
        try:
            queue.put_nowait((event, metadata['seq']))
        except (asyncio.QueueFull, KeyError):
            fatal.set()

    @sio.on('delivery_error')
    def delivery_error(error):
        fatal.set()  # Do not silently skip a replay gap or invalid subscription.

    try:
        await sio.connect(ORIGIN, socketio_path=f'/v1/projects/{bridge.api.project}/events/ws',
                          transports=['websocket'], headers={'Authorization': f'Bearer {bridge.api.key}',
                                                           'User-Agent': 'Pinpoint/1.0'})
        while not fatal.is_set():
            try:
                event, seq = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            else:
                # A single worker serializes mutations. Failure stops before later cursors.
                await asyncio.to_thread(bridge.process, event, seq)
            await asyncio.to_thread(bridge.finish_jobs)
            await asyncio.to_thread(bridge.relay)
        raise PhotonError('Photon stream stopped; restart to resume from the saved cursor')
    finally:
        await sio.disconnect()
        bridge.pool.shutdown(wait=True)


def main(argv=None):
    from perception.matterport import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--console', default='http://localhost:4175')
    parser.add_argument('--state', type=Path, default=STATE)
    parser.add_argument('--check', action='store_true', help='Verify credentials and platform configuration without sending')
    args = parser.parse_args(argv)
    key, project = os.getenv('PHOTON_API_KEY', ''), os.getenv('PHOTON_PROJECT_ID', '')
    if not key or not project:
        parser.error('Set PHOTON_API_KEY and PHOTON_PROJECT_ID in .env or the environment.')
    try:
        api = Photon(key, project)
        if args.check:
            resources = api.call('GET', '/platforms/resources').get('resources', [])
            print(json.dumps({'projectId': project, 'resources': resources}, indent=2))
            if not any(r.get('state') == 'active' for r in resources):
                parser.exit(1, 'No active Photon channel. Complete channel setup in the dashboard.\n')
            return
        console = Console(args.console)
        if not console.alive():
            parser.error('Start python -m live.serve first.')
        packet_bot = None
        if os.getenv('PHOTON_PACKET_VIA_TELEGRAM') == '1':
            token = os.getenv('TELEGRAM_BOT_TOKEN', '')
            if not token:
                parser.error('PHOTON_PACKET_VIA_TELEGRAM=1 requires TELEGRAM_BOT_TOKEN.')
            packet_bot = Telegram(token)
            packet_bot.username = packet_bot.call('getMe')['username']
        bridge = PhotonBridge(api, console, args.state,
            allowed_handles=filter(None, os.getenv('PHOTON_ALLOWED_HANDLES', '').split(',')),
            agent_handle=os.getenv('PHOTON_AGENT_HANDLE', ''), packet_bot=packet_bot)
        # One process per durable cursor. flock releases automatically after a crash.
        import fcntl
        args.state.parent.mkdir(parents=True, exist_ok=True)
        with args.state.with_suffix('.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            asyncio.run(run_stream(bridge))
    except (PhotonError, ValueError, OSError) as exc:
        parser.exit(1, f'{exc}\n')
    except (KeyboardInterrupt, asyncio.CancelledError):
        print('Photon bridge stopped.')


if __name__ == '__main__':
    main()
