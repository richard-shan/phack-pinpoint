# Synthetic fixture adapter handoff

This document describes `index.html` and the static fixture server only. For the disk-backed service at `live.html`, see [LOCAL-CONTRACT.md](LOCAL-CONTRACT.md).

This is a proposed UI adapter boundary, not an agreed team API. Confirm with C before implementing transport. `fixtures.js` is the current shape reference. Keep rendering independent of backend file paths and vendor SDKs.

## Current adapter interface

`src/app.js` imports `createDemoAdapter()` from `src/adapter.js`:

| Method | Purpose |
| --- | --- |
| `getIncident()` | Snapshot containing building, caller messages/photo, location, route profiles, evidence, packet, revision, status and decision. |
| `subscribe(listener)` | Publish a fresh snapshot on each update; returns unsubscribe. |
| `sendMessage(text)` | Add an outbound dispatcher message. Replace with Photon delivery through your server. |
| `savePacket({notes, unconfirmed})` | Save a new revision; invalidate approval. |
| `decide('approved' \| 'rejected', reason?)` | Resolve dispatcher review. Rejection requires a reason. |
| `getPersistenceWarning()` | User-visible storage status. Replace with connection/save errors. |
| `dispose()` | Cancel owned subscriptions/timers. |

`replay`, `simulateCallerReply`, `reset`, and `acceptExternal` are demo controls. Remove or hide them in a backend adapter. Uploaded images currently call `replay(dataUrl)`; a backend integration needs a separate `uploadCallerPhoto(file)` flow returning an attachment ID and progress/error state.

The fixture adapter is synchronous. Real network mutations must be awaited in `app.js` with pending/error UI; do not blindly swap in async methods. Subscribe to agent updates using SSE or WebSocket, or poll if the team prefers. The current numeric `stage` assumes a linear demo; concurrent agents need per-agent status/events instead.

## Team artifact mapping

| Team artifact | UI fields |
| --- | --- |
| `intake.json` | incident ID, address, caller photo, initial text |
| `brief.json` | building profile, suite tenant, source record |
| `candidates.json` / `location.json` | ranked matches, chosen node, floor, confidence, match evidence |
| `site.json` | aerial URL, entrance/staging coordinates, obstructions |
| `route.json` | route geometry, steps, thumbnails, meters/seconds, foot and stretcher profiles |
| `audit.json` | supported claims, source references, unknowns |
| `packet.md` | output narrative; preferably also provide a structured packet payload for this renderer |
| `trace.jsonl` | agent and tool events, real durations and timestamps |

The console currently renders a structured packet and **exports** Markdown. It does not parse an arbitrary `packet.md` document. If C only provides Markdown, agree on structured front matter or add a sanitized Markdown renderer. Never inject backend Markdown or messages as raw HTML.

## Fields still needed from A/B/C

- Location and route map coordinates in a documented coordinate system, floor metadata, thumbnail/image URLs, source evidence IDs. Replace `floorMap()` and `siteMap()` illustrations in `views.js` with actual geometry and images.
- Per-claim evidence and confidence. Current fixture source links are grouped by field; every real claim should resolve to a supporting record/crop/tile, or remain unconfirmed.
- Stable incident ID, revision/version, caller-channel ID, approval interrupt ID, delivery status, and timestamp.
- An explicit `awaitingCaller` event plus question ID. Caller replies should arrive via backend events; a dispatcher’s outgoing message is not a caller response.
- Production packet URL and immutable approved revision. A localhost hash URL is only a preview.

## Approval and delivery

`review → approved` and `review → rejected`; editing any completed packet produces a new `review` revision. `running` cannot be approved. These rules are currently local UI/demo checks, not a security boundary.

The backend must enforce revision matching, authentication, authorization, and idempotency when it resumes C’s approval interrupt. A stale approval must not send a newer revision. Keep **approved**, **sending**, **delivered**, and **failed** separate in the real integration. This prototype only records **approved locally** and never calls `send_packet` or `notify_contact`.

The local server only serves static assets. Do not put provider tokens in browser files. Implement uploads, Photon webhooks, outgoing messages, and agent invocation in the team backend.
