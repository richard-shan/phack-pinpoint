> **Real-data build:** Run `npm run dev:real` from `app/` and open http://127.0.0.1:4175. This mode uses real CoStar records, cached Matterport imagery, and persistent local incidents. See [REAL-DATA.md](REAL-DATA.md) for the complete setup. The documentation below describes the separate synthetic fixture demo.

> **Fresh checkout:** Follow [real-data setup](REAL-DATA.md#start) first. Spatial demos require ignored scan graphs, the shared viewer bundle, and provider credentials; run both setup commands there and use `localhost` for the SDK viewer.

# Pinpoint · D-lane console

Local dispatcher console and responder packet, built against synthetic fixtures. No external services, dependencies, or credentials are needed.

## Run locally

Requires Node.js 20 or later. From the repository root:

```sh
cd app
npm run dev
```

Open **http://localhost:4173**. To use another port: `PORT=4174 npm run dev`.

The server binds to loopback only. Keep it running while reviewing. Stop with Ctrl+C. There is no install or build step: browser-native ES modules and CSS are served directly. `npm run check` checks JavaScript syntax; `npm test` exercises the incident state and approval flow.

## Review the demo

1. The initial incident is ready for dispatcher review. Inspect **Interior route**, **Site approach**, and **Photo match**. Switch between **On foot** and **Stretcher**; the diagram, entrance, staging, and directions change together.
2. Caller conversation stays beside the route on desktop. Open any evidence link to inspect a fixture record. Expand **Building, sources & agent activity** for supporting records and the incident trace.
3. **Edit packet** changes dispatcher notes and unconfirmed items. **Approve packet** records local approval. **Reject** requires a reason; edit the rejected packet to submit a new revision. Editing an approved packet invalidates its approval.
4. **Open full packet** opens `#packet/PP-1042`. Print / PDF uses a compact print layout; Markdown exports both route profiles. **Copy local link** is enabled only for an approved revision.
5. **Replay incident** resets the fixture and plays agent stages. It pauses at the localization question; choose **Simulate “yes” reply** to continue. The message composer adds outgoing dispatcher messages locally; it does not advance the caller question.
6. **Try a caller photo** accepts PNG, JPG, or WebP up to 2 MB, previews it, and starts a fixture replay. No actual image matching happens. The original fixture panorama remains alongside the upload.
7. **Reset** restores the original incident after a confirmation. Browser refresh preserves edits and decisions through localStorage. A paused replay resumes at the caller question.

## Scope and limitations

- All property details, confidence, route geometry/timing, equipment, agent durations, and images are **synthetic**. The maps are local SVG schematics; the lobby is an AI-generated synthetic PNG. Neither represents actual Matterport or Vexcel assets.
- No Photon, CoStar, Matterport, Vexcel, deepagents, geolocation, actual routing, or delivery service is connected. Approving does not send a packet or message.
- The local link resolves the latest saved revision in **the same browser and device**, while the server runs. It is not a public or immutable share URL. Other devices/browsers do not share localStorage.
- Treat a single console tab as the demo writer. Packet tabs listen for storage changes. There is no concurrent-edit conflict handling.
- Uploaded images, notes, messages, and decisions remain in browser localStorage. Reset removes the upload from this demo state. Storage errors are shown; the current tab still works, but local link copying is blocked.
- Packet editing currently covers notes and unknowns. Structured location/route corrections should come from the team’s backend; the console does not fabricate revised graph paths.
- The default fixture is designed to print to one A4 page. Long dispatcher notes or many unknowns can expand the printout to additional pages.

## Files

```text
app/
  index.html          Browser entry
  server.mjs          Loopback-only static server
  src/
    fixtures.js       Synthetic incident and agent stages
    adapter.js        Observable demo state, approval rules, Markdown export
    app.js            Event handlers, dialogs, navigation, uploads
    views.js          Console, packet, and SVG map views
    tokens.css        Color/type tokens
    styles.css        Responsive screen and print styles
  assets/             Local mark, synthetic lobby image, licensed Public Sans font
  tests/              Node-native behavioral checks
  BACKEND.md          Integration seam and proposed contract
  DESIGN.md           Implemented palette, typography, and component rules
```

Application work lives in `app/`. Generated data, provider assets, runtime state and local authoring tools are excluded from this contribution. Integration with the other lanes is described in [LOCAL-CONTRACT.md](LOCAL-CONTRACT.md).

## Princeton 3D route demo

Run `npm run dev:real`, then open **http://localhost:4175/live.html#princeton**. Use `localhost` (the sponsor SDK key is scoped to that hostname). The Princeton demo opens Rabinowitz Hall in the self-hosted Matterport Bundle SDK, draws paths in the scan's world coordinates, and offers satellite approach, floor selection, route following, entry/destination changes, blocked segments, and confirmed elevator/stair connections. Route updates persist, refresh in other consoles, and invalidate packet approval.

See [SPATIAL-ROUTES.md](SPATIAL-ROUTES.md) for setup, the agent route API, and geometry limitations.
