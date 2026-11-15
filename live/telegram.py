"""Telegram caller bridge. Run alongside live.serve and live.run --watch."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

from .console_api import Console, ConsoleError
from env.graph import list_buildings
from .delivery import approved_packet, packet_text

STATE = Path(__file__).resolve().parent.parent / 'cache' / 'telegram.json'
MAX_DOWNLOAD = 20 * 1024 * 1024
WELCOME = (
    "Hi, I’m Pinpoint. Let’s help locate you.\n\n"
    "Send a photo of what’s around you — a hallway, doorway, or room is a good place to start.\n\n"
    "I’ll look for a match in the buildings I know and prepare the location and route for dispatcher review. "
    "You can add details after your photo."
)


class TelegramError(RuntimeError):
    pass


class InvalidPhoto(TelegramError):
    pass


class Telegram:
    def __init__(self, token):
        self.token = token

    def call(self, method, **body):
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{self.token}/{method}',
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.load(response)
        except (OSError, ValueError):
            # urllib errors can contain the credential-bearing URL.
            raise TelegramError(f'Telegram {method} failed; check connectivity and bot configuration') from None
        if not result.get('ok'):
            raise TelegramError(f'Telegram {method} failed (code {result.get("error_code")})')
        return result['result']

    def send(self, chat, text):
        return self.call('sendMessage', chat_id=chat, text=text[:4000])

    def send_packet(self, chat, snapshot):
        """Send all reviewed content as a document; localhost links cannot reach phones."""
        boundary = 'pinpoint-' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n'
                f'Approved demo packet · revision {snapshot["incident"]["revision"]}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
                f'filename="{snapshot["id"]}.md"\r\nContent-Type: text/markdown; charset=utf-8\r\n\r\n'
                + packet_text(snapshot) + f'\r\n--{boundary}--\r\n').encode()
        req = urllib.request.Request(f'https://api.telegram.org/bot{self.token}/sendDocument',
            data=body, headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.load(response)
        except (OSError, ValueError):
            raise TelegramError('Telegram packet upload failed; delivery may be uncertain') from None
        if not result.get('ok'):
            raise TelegramError('Telegram rejected the packet upload')
        return {'status': 'accepted', 'messageId': result['result']['message_id']}

    def photo(self, file_id):
        info = self.call('getFile', file_id=file_id)
        if info.get('file_size', 0) > MAX_DOWNLOAD:
            raise InvalidPhoto('Photo is too large')
        path = info.get('file_path', '')
        if not path or '..' in path or path.startswith('/'):
            raise TelegramError('Telegram returned an invalid file path')
        try:
            with urllib.request.urlopen(
                    f'https://api.telegram.org/file/bot{self.token}/{path}', timeout=30) as response:
                raw = response.read(MAX_DOWNLOAD + 1)
        except OSError:
            raise TelegramError('Telegram photo download failed') from None
        if len(raw) > MAX_DOWNLOAD:
            raise InvalidPhoto('Photo is too large')
        return raw


class Bridge:
    def __init__(self, telegram, console, state_path=STATE, allowed_chats=(), detector=None):
        self.telegram, self.console = telegram, console
        self.path = Path(state_path)
        self.allowed = {str(c) for c in allowed_chats}
        self.buildings = set(list_buildings()) - {'toy'}
        if detector is None:
            from perception.identify import identify_building
            detector = identify_building
        self.detector = detector
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.jobs = {}
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'offset': 0, 'chats': {}}
        for session in self.state['chats'].values():
            if 'photos' not in session:
                old = {k: session.pop(k) for k in list(session) if k != 'building'}
                session['photos'] = {'legacy': old} if old else {}
                if old:
                    session['active'] = 'legacy'

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def send(self, chat, text, key):
        if hasattr(self.telegram, 'send_once'):
            return self.telegram.send_once(chat, text, key)
        return self.telegram.send(chat, text)

    @staticmethod
    def active(session):
        return session.get('photos', {}).get(session.get('active'))

    def flush_replies(self, record):
        if not record.get('incident'):
            return
        for reply in record.get('replies', []):
            if not reply.get('delivered'):
                self.console.message(record['incident'], 'caller', reply['text'])
                reply['delivered'] = True
                self.save()

    def handle(self, update):
        message = update.get('message', {})
        chat = message.get('chat', {})
        if chat.get('type') != 'private':
            return
        chat_id = str(chat['id'])
        if self.allowed and chat_id not in self.allowed:
            return
        session = self.state['chats'].setdefault(chat_id, {'photos': {}})
        record = self.active(session)
        text = message.get('text', '').strip()
        command, _, argument = text.partition(' ')
        command = command.split('@')[0]
        send = lambda value: self.send(chat_id, value, f'inbound:{message["message_id"]}')
        if command in ('/start', '/help'):
            send(WELCOME)
        elif command == '/buildings':
            send('You don’t need to choose a building. Just send a photo.\n\n'
                 'If you already know it, use /building ID for your next photo.\n'
                 'Available building IDs: ' + ', '.join(sorted(self.buildings)))
        elif command == '/building':
            if argument.strip() not in self.buildings:
                send('Choose a supported building ID: ' + ', '.join(sorted(self.buildings)))
            else:
                session['building'] = argument.strip()
                self.save()
                send('Building selected for your next photo. Text still belongs to your most recent photo.')
        elif command == '/new':
            session.pop('building', None)
            self.save()
            send('Your next photo will identify its building automatically. Every photo starts a new incident.')
        elif command == '/status':
            if record and record.get('pending'):
                send('Checking your latest photo against the locally indexed buildings.')
            elif record and record.get('incident'):
                inc = self.console.get(record['incident'])
                send(f'Incident {inc["id"]}: {inc.get("status", "review")}')
            elif record:
                send('The latest photo has no identified building. Its follow-up text is saved with that photo.')
            else:
                send('Send a photo to start an incident.')
        elif message.get('photo'):
            key = str(message['message_id'])
            if key not in session['photos']:
                photo = max(message['photo'], key=lambda p: p['width'] * p['height'])
                raw = self.telegram.photo(photo['file_id'])
                directory = self.path.parent / 'telegram-photos'
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / f'{chat_id}-{key}.jpg'
                path.write_bytes(raw)
                path.chmod(0o600)
                pending = {'path': str(path.resolve()), 'caption': message.get('caption', '')}
                building = session.pop('building', None)
                if building:
                    pending['result'] = {'status': 'matched', 'building_id': building, 'reason': 'selected'}
                session['photos'][key] = {'pending': pending, 'replies': []}
                session['active'] = key
                self.save()
            send('Got your photo.\n\nI’m checking it against the buildings I know. This may take a minute.\n\n'
                 'You can send more details while I look. If I can’t find a reliable match, I’ll tell you.')
        elif command.startswith('/'):
            send('Unknown command. Use /help.')
        elif text and record is not None:
            replies = record.setdefault('replies', [])
            if (record.get('last_reply') != message['message_id']
                    and not any(r['id'] == message['message_id'] for r in replies)):
                replies.append({'id': message['message_id'], 'text': text})
                self.save()  # bind to this photo before any network write
            self.flush_replies(record)
        else:
            send(WELCOME)

    def finish_jobs(self):
        for chat, session in self.state['chats'].items():
            if self.allowed and chat not in self.allowed:
                continue
            for key, record in session['photos'].items():
                pending = record.get('pending')
                if not pending:
                    self.flush_replies(record)
                    continue
                job = (chat, key)
                if 'result' not in pending:
                    if job not in self.jobs:
                        self.jobs[job] = self.pool.submit(self.detector, pending['path'], self.buildings)
                    if not self.jobs[job].done():
                        continue
                    try:
                        pending['result'] = self.jobs.pop(job).result()
                    except Exception as exc:
                        print(f'Building detection failed: {type(exc).__name__}', flush=True)
                        pending['result'] = {'status': 'unavailable'}
                    self.save()
                result = pending['result']
                record['identification'] = result
                if result['status'] == 'matched':
                    building = result['building_id']
                    if building not in self.buildings:
                        raise ValueError('Detector returned an unsupported building')
                    if not record.get('incident'):
                        inc = self.console.create(building, pending['caption'], pending['path'])
                        record.update(incident=inc['id'], sent=[m['id'] for m in inc.get('messages', [])])
                        self.save()
                    self.flush_replies(record)
                    text = 'I’ve found a likely location from your photo. It’s ready for dispatcher review.'
                elif result['status'] == 'unknown':
                    text = ('I couldn’t reliably identify the location in your photo. '
                            'Try another view with a doorway, sign, or other distinctive feature.')
                else:
                    text = 'Photo matching is temporarily unavailable. Please try sending your photo again later.'
                self.send(chat, text, f'identification:{key}')
                Path(pending['path']).unlink(missing_ok=True)
                record.pop('pending')
                self.save()

    def relay(self):
        for chat, session in self.state['chats'].items():
            if self.allowed and chat not in self.allowed:
                continue
            for key, record in session['photos'].items():
                if not record.get('incident'):
                    continue
                try:
                    inc = self.console.get(record['incident'])
                    sent = record.setdefault('sent', [])
                    for message in inc.get('messages', []):
                        if message.get('role') == 'dispatcher' and message['id'] not in sent:
                            prefix = ''
                            if key != session.get('active'):
                                prefix = 'About an earlier photo (your replies go with your latest photo):\n\n'
                            self.send(chat, prefix + message['text'], f'dispatcher:{message["id"]}')
                            sent.append(message['id'])
                            self.save()
                    packet_id = inc.get('packetId')
                    deliveries = record.setdefault('packets', {})
                    if packet_id and packet_id not in deliveries and hasattr(self.telegram, 'send_packet'):
                        snapshot = approved_packet(self.console, inc)
                        if snapshot:
                            # Bot API uploads have no idempotency key. If a request times
                            # out after Telegram accepted it, automatic retry can double-send.
                            retry_safe = getattr(self.telegram, 'packet_idempotent', False)
                            if not retry_safe:
                                deliveries[packet_id] = {'status': 'sending'}
                                self.save()
                            try:
                                deliveries[packet_id] = self.telegram.send_packet(chat, snapshot)
                            except TelegramError:
                                if not retry_safe:
                                    deliveries[packet_id] = {'status': 'uncertain',
                                        'action': 'Check Telegram before manually retrying this packet.'}
                                    self.save()
                                raise
                            self.save()
                except (ConsoleError, OSError, TelegramError):
                    print('Could not relay an incident message; will retry.', flush=True)

    def poll(self):
        updates = self.telegram.call('getUpdates', offset=self.state['offset'], timeout=2,
                                     allowed_updates=['message'])
        for update in updates:
            if update['update_id'] < self.state['offset']:
                continue
            try:
                self.handle(update)
            except (ConsoleError, InvalidPhoto) as exc:
                if isinstance(exc, ConsoleError) and exc.status not in (400, 404, 413):
                    raise
                chat = update.get('message', {}).get('chat', {}).get('id')
                if chat is not None:
                    self.telegram.send(chat, 'Could not accept that message or photo. Please try again.')
            self.state['offset'] = update['update_id'] + 1
            self.save()
        self.finish_jobs()
        self.relay()


def main(argv=None):
    from perception.matterport import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--console', default='http://localhost:4175')
    parser.add_argument('--state', type=Path, default=STATE)
    args = parser.parse_args(argv)
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        parser.error('Set TELEGRAM_BOT_TOKEN in the environment first.')
    console = Console(args.console)
    if not console.alive():
        parser.error('Start the console with python -m live.serve first.')
    telegram = Telegram(token)
    try:
        me = telegram.call('getMe')
        if telegram.call('getWebhookInfo').get('url'):
            parser.error('This bot has a webhook configured. Use a dedicated polling bot or remove its webhook first.')
    except TelegramError as exc:
        parser.error(str(exc))
    bridge = Bridge(telegram, console, args.state,
                    filter(None, (c.strip() for c in os.environ.get('TELEGRAM_ALLOWED_CHAT_IDS', '').split(','))))
    print(f'Telegram @{me["username"]} connected. Run live.run --watch for photo processing.', flush=True)
    try:
        while True:
            try:
                bridge.poll()
            except (TelegramError, ConsoleError, OSError):
                print('Telegram bridge request failed; retrying in 5 seconds.', flush=True)
                bridge.relay()
                time.sleep(5)
    except KeyboardInterrupt:
        print('\nTelegram bridge stopped.')


if __name__ == '__main__':
    main()
