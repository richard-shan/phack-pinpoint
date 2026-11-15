# Photon 2.0 messaging integration

`python -m live.photon` uses the **Beta 2.0** API at `https://api.photon.codes/v1`.
It does not use the legacy Spectrum SDK. Both Telegram and iMessage direct
conversations are supported. Photon is the actual ingress and reply transport,
not just a dependency listed in the submission.

## Send your photo through iMessage

Enroll the phone number (E.164, including `+` and country code) or Apple ID email
that you send iMessages from. This requires `platforms:read` and `platforms:write`:

```sh
python -m live.photon --assign-imessage +15555550123
python -m live.photon --check
```

Enrollment prints your `assignedPhoneNumber`, reuses an existing assignment,
and sends no invitation or message. Each caller may get a different destination.
Start the console, agent, and bridge using the commands below, then open Messages
on your iPhone and send a photo to **your assigned number**. Your first message
opts you in. Text accompanying the image is included; later replies follow the
latest photo. Dispatcher questions and the approved PDF return to that same
iMessage conversation, even if the legacy Telegram packet override is enabled.
Use JPEG or PNG if your phone's original image format cannot be decoded.

Leave `PHOTON_AGENT_HANDLE` blank when callers have different assigned lines.
If `PHOTON_ALLOWED_HANDLES` is set, include your exact enrolled handle. Clear old
Telegram-only values when switching. `--check` now checks iMessage enrollment;
it prints opt-in state and does not prove live delivery. Use
`--check --platform telegram` for the existing Telegram channel.

## Run

Install the existing environment/perception requirements plus
`python -m pip install -r live/requirements.txt`. Put `PHOTON_PROJECT_ID` and
`PHOTON_API_KEY` in the ignored root `.env` (or export them). The key needs
`project:read`, `project:write`, `messages:read`; `--check` additionally needs
`platforms:read`. Set `PHOTON_PACKET_VIA_TELEGRAM=1` to send approved Markdown documents with the
existing `TELEGRAM_BOT_TOKEN`. Callers must have started that bot as well as the
Photon-managed intake bot; their numeric Telegram identity binds the destination,
never a phone number or a user-supplied chat ID. Without this option, packets use
Photon in the original conversation.

Optional `PHOTON_AGENT_HANDLE` pins the receiving bot/line;
`PHOTON_ALLOWED_HANDLES` restricts callers to comma-separated Telegram numeric
user IDs or iMessage handles. Empty permits all direct human callers.

Run in separate terminals:

```sh
.venv/bin/python -m live.serve
.venv/bin/python -m live.run --watch
.venv/bin/python -m live.photon --check
.venv/bin/python -m live.photon
```

For Telegram instead, connect the bot through the Photon dashboard first. The current Beta
`POST /platforms/telegram/bots` takes an empty body and returns a managed-bot
setup link; it does **not** accept an arbitrary BotFather token. Do not run
`live.telegram` polling against a bot whose webhook is owned by Photon. The
existing token-based `live.telegram` remains available as a separate transport,
and now sends approved packets as Markdown document uploads.

## Demo and evidence

1. Open a private conversation with the connected bot. No commands or building selection
   are needed (Telegram may require tapping **Start** on first contact).
2. Send a photo with a caption. The bridge downloads the attachment through
   Photon's authenticated API, validates it, identifies a supported building,
   and creates an incident containing the photo and caption. Every new photo starts
   a separate case. All subsequent text belongs to the latest photo, including while
   identification is pending and across bridge restarts. An unknown photo retains
   its own text and never sends it to an earlier incident; only a reliable building
   match creates a routable console incident.
   `/building 6550930` is an optional one-photo diagnostic override, not part of the normal flow.
3. Watch the agent process the incident in the console. Its dispatcher questions
   are sent through Photon; text replies return to the same incident. Each new
   photo starts a new incident; replies belong to the latest photo.
4. Review the location, route, uncertainty, and packet. Before approval, no
   packet is sent. Rejection sends no packet. `--auto-approve` evaluation packets
   are excluded from external delivery.
5. Approve in the dispatcher console. The bridge retrieves the immutable saved
   snapshot and verifies its revision is still approved. The **complete** packet
   is sent as a `.md` document through the existing Telegram bot when
   `PHOTON_PACKET_VIA_TELEGRAM=1`; otherwise it is sent as one PDF attachment
   through Photon to the original caller, with a short caption. The default needs no second bot. No localhost link is
   presented as a remotely accessible packet.
6. Save a screen recording showing photo arrival, the agent's reply, the approval,
   and packet receipt in Telegram. Photon API Activities and the local message
   IDs provide independent transport evidence. API acceptance alone is not
   proof the recipient received a message.

The public HackPrinceton challenge is “Build a Messaging Agent using Photon
2.0”; Best Voice Agent is separate. This flow demonstrates Photon messaging,
photo understanding, a useful agent task, and an approval-controlled output.
The detailed sponsor guidelines explicitly require a Photon 2.0 project live on
at least one Photon channel. They also describe iMessage as the flagship and label
the main awards iMessage Track Winner/Runner-Up; confirm any award-specific
interpretation with the sponsor. The demonstrated Telegram transport meets the
stated at-least-one-channel condition; it is not an iMessage or voice demo.

## Recovery and delivery semantics

`cache/photon.json` contains the project-specific stream cursor, caller/incident
bindings, processed message IDs and outgoing receipts. Keep it across restarts;
only one process may use it. Incoming events are processed in order. An error
stops processing before later cursors can be committed. Restart after correcting
the cause; the same cursor resumes retained events. Photon retention is finite:
a replay-gap error needs investigation, never a silent cursor reset.

Outgoing writes use stable idempotency keys and saved message IDs. `dispatched`
and `accepted` are distinct from `delivered`; delivery/failure events update the
outbound receipt. A failed message is retained as failed rather than silently
issued again with a new key. For a deliberately reviewed resend, approve a new
revision. PDF uploads and sends have separate stable idempotency keys. Saved attachment IDs prevent
repeat uploads; approval is checked again after upload. Existing multipart delivery receipts
are retained and do not trigger an automatic resend.

The direct Telegram Bot API cannot guarantee exactly-once sends. Its bridge
records `sending` before upload and `accepted` with the Telegram message ID
afterwards. An ambiguous failure records `uncertain` and suppresses automatic
retry. Check the destination chat before deleting only that packet's receipt in
`cache/telegram.json` while the bridge is stopped. Never delete the whole state
file as a retry mechanism.

Edits invalidate approval. Delivery uses only a reviewed snapshot; an edit after
an external send starts cannot recall messages already in flight. Local state
and photos are private files and must not be committed.

## Checks

```sh
.venv/bin/python -m unittest live.tests.test_photon live.tests.test_telegram live.tests.test_delivery -v
```

Tests run the real console server with fake provider responses. They verify
caption/photo intake, caller isolation, reply routing, replay deduplication,
idempotent retries, payload version checks, complete PDF packets, upload retries and approval checks after upload, rejection,
auto-approval suppression, changed revisions, and delivery status updates.
These tests do not replace a live provider round trip.

References: [Photon Beta event stream](https://photon.codes/docs/beta/message-delivery/websocket.md),
[API schema](https://api.photon.codes/openapi.json),
[HackPrinceton prizes](https://hackprinceton-fall-26.devpost.com/),
[detailed sponsor guidelines](https://somethingreat.notion.site/Hackathon-Guidelines-for-Photon-2-0-3f8e443f36bb81f9893fed0d08a4c01d).

## Live verification — November 14, 2026

Verified in the existing Photon project and Telegram account:

- Active Photon-managed channel: https://t.me/pinpoint_photon_princeton_bot.
- Original packet bot: https://t.me/pinpoint_princeton_demo_bot.
- Received a real Telegram photo via Photon, downloaded its attachment, and
  created a console incident; the running agent produced the matching route.
- Sent a dispatcher question through Photon and received a caller response back
  into the same console incident. Photon reported outgoing messages delivered.
- Approved the explicit demo test incident; a 7.4 KB Markdown packet appeared in
  the original packet bot chat. A second photo verified caption metadata parsing.
- No packet was sent for the second incident while it remained under review.

The first test photo exposed Telegram's `content.metadata.caption.text` mapping;
the implementation and regression test now cover it. Both tests used a known
local demo image and a manually selected building, so they verify transport and
agent execution, not open-world building identification accuracy.

The local ignored `cache/photon-verification.json` retains incident/packet/message
IDs for audit. Credentials stay in `.env`, never in this document.

## PDF delivery

With `PHOTON_PACKET_VIA_TELEGRAM=0` (default), approvals send one PDF file card in the
original conversation, captioned “Here’s your location and route, reviewed by the dispatcher.”
The document contains the approved summary and full reviewed content, including uncertainty
and source references. It does not fetch local evidence URLs from the recipient’s phone.
`live.packet_document` uses deterministic PDF output so upload retries keep identical bytes.
Install `live/requirements.txt` before starting the bridge.

## iMessage verification — November 14, 2026

Enrolled a caller using the Photon dashboard and received a real photo through
that caller's assigned shared iMessage line. The bridge downloaded and processed
the image. Building identification returned `unknown` with `weak_evidence`, so
no dispatch incident was created. This verifies live photo transport, not a
successful location match or live approved-PDF delivery. The existing API key
could list assignments but returned HTTP 403 when creating one; dashboard
enrollment worked without changing the key.

## Demo: location travels with the photo (simulated carrier ping)

A real 911 call carries the phone's network location; a texted photo does not, and
iMessage/Telegram strip or rewrite EXIF. For the demo, the location is hidden in the
pixels (`perception/geomark.py`), which survives the apps' resize and JPEG re-encode.
**This is simulated location for a demo, and the console labels it so.**

Before the demo, tag each photo with the place it was taken:

```sh
.venv/bin/python -m live.geotag buildings                          # what a location can resolve to
.venv/bin/python -m live.geotag tag IMG_0412.jpg --building princeton-rabinowitz
.venv/bin/python -m live.geotag tag IMG_0413.jpg --address "7900 Fannin"
.venv/bin/python -m live.geotag read demo-tagged/IMG_0412.jpg      # check one
```

AirDrop `demo-tagged/*.jpg` to the phone and text them as normal photos. When a tagged
photo arrives, the bridge picks the nearest building with a graph (within 400 m), creates
the incident at once (no building choice, no photo-matching wait), and replies with a
short first-responder message ("We have your message and your location near ... Help
is on the way."). That reply is also saved in the console thread as an automatic reply.
The agent watcher then builds the location, route, 3D view and packet as before.
Untagged photos still go through photo matching.

The console's home page (`/live.html`) is now the incoming-message inbox: one thread per
photo with the picture, every message after it, and the caller's location. Opening a
thread shows the existing route, 3D model, packet and approval.
