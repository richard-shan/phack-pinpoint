# Local D-lane contract

Implemented and tested independently of A/B/C. This is a concrete proposal for team integration, not an agreed team API. Start `npm run dev:real`; default origin is `http://127.0.0.1:4175`.

## HTTP endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Local service status; externalDelivery is false. |
| GET | `/api/catalog` | Catalog properties and source mapping. |
| GET | `/api/incidents` | Saved incidents; list entries omit image bytes. |
| POST | `/api/incidents` | Create intake from property ID, text and optional photo. |
| GET | `/api/incidents/:id` | Full incident. |
| PATCH | `/api/incidents/:id` | Save manual review/import/photo as a new revision. |
| POST | `/api/incidents/:id/messages` | Append a local caller or dispatcher message. |
| POST | `/api/incidents/:id/decision` | Approve or reject the exact current revision. |
| GET | `/api/packets/:id` | Immutable approved incident plus property snapshot. |

Writes use `Content-Type: application/json`. Create returns 201 and an incident. Updates and decisions return 200 and the updated incident. Errors return `{ "error": { "code": "...", "message": "..." } }`. A stale revision returns 409. Validation failures return 400, large requests 413, wrong content type 415. This loopback service is not a remotely authenticated deployment.

## Payloads

Create:

```json
{"propertyId":"350372","callerText":"DEMO: caller description","photo":{"name":"caller.jpg","dataUrl":"data:image/jpeg;base64,..."}}
```

Photo is optional; text or photo is required. Accepted formats are PNG, JPEG and WebP, up to 2 MiB. Contents must match the declared image type. Text limit is 4,000 characters.

Edit (all fields except expectedRevision are optional; provide at least one):

```json
{"expectedRevision":1,"location":"Caller reports lobby; not independently confirmed.","route":"","notes":"Request floor and suite.","unconfirmed":["Entrance and route not verified."],"packetMarkdown":"# Draft packet\nNo route confirmed."}
```

Location limit 1,000; notes and route 10,000 each; Markdown 102,400 characters; up to 50 unknowns, each 1,000 characters. The browser limits imported files to 100 KiB. PATCH also accepts the same photo object, or null to remove it. The UI supports replacing a photo.

Message:

```json
{"expectedRevision":2,"role":"dispatcher","text":"Which floor are you on?"}
```

`role` is `caller` or `dispatcher`; text limit 4,000. This is a local test channel, not a Photon transport. Maximum 200 messages per incident.

Decision:

```json
{"expectedRevision":3,"decision":"approved"}
```

or `decision: "rejected"` with a nonempty `reason` (up to 2,000 characters). Only review status accepts a decision. A decision keeps the content revision; edits/messages/photos increment it and invalidate the decision. Rejected packets reopen through editing. `updatedAt` changes for decisions as well as edits.

## Snapshot guarantees

Approved snapshots contain `{id, approvedAt, incident, property}`. The URL is `/live.html#packet/<packet-id>`. Each approval creates a new snapshot; later incident edits and later catalog changes do not rewrite it. The image assets themselves are source files; preserve their bytes when retaining old snapshots. The portable HTML export embeds the displayed images for a self-contained artifact.

URLs work across browsers on this computer while the server runs. They are not public links and cannot reach a teammate's computer over localhost. Exports can be handed off separately when the user chooses.

## A/B/C integration seam

- `propertyId` must be an actual catalog ID. Match source IDs and addresses; do not join on proximity alone.
- Accept `packet.md` through the implemented import now. The minimal renderer supports text, headings, and bullets; it deliberately does not execute raw HTML or fetch embedded resources.
- Supply structured `location.json`, `site.json`, `route.json`, `audit.json`, and per-claim evidence alongside the Markdown. The current manual fields are not a parser for these files.
- Agree on route coordinate systems, floor IDs, graph edges, profile definitions, image URLs, confidence semantics, and evidence IDs before replacing unknowns with geometry.
- Supply agent events with incident ID, event ID, timestamps and status. Current event entries describe actual local service operations only.
- Caller/dispatcher roles are test labels, not authentication. Real deployments need identity, authorization, connection errors, and durable channel delivery state.
- A real approval bridge must compare this revision with C's interrupt/version before resuming. Never wire approval to a newer packet. Keep approved, sending, delivered, and failed separate.
- Keep provider credentials, image processing, and external message transport server-side. No `send_packet` or `notify_contact` call exists here.

The service and its tests establish the D-side behavior without claiming to have implemented the other lanes.
