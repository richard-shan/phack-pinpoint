# Pinpoint: local real-data workspace

The complete independent D-lane build lives at `live.html`, served by the local incident service. It uses real supplied CoStar records and cached Matterport evidence. The original synthetic console remains in `index.html` and is maintained separately.

## Start

Requires Node.js 20+ and Python 3 for catalog generation. No npm install is needed. The catalog workspace uses locally imported sponsor data; the 3D and aerial demos additionally require generated assets and provider credentials.

For a fresh checkout, first build the catalog from your authorized downloaded bundle:

```sh
cd app
python3 scripts/build-catalog.py --source /absolute/path/to/.local-data
```

See [data provenance](DATA-PROVENANCE.md#rebuild) for required inputs and optional cached images. To enable both spatial demos, set these values in the ignored `app/.env.local` file (use your own credentials):

```dotenv
MATTERPORT_SDK_KEY=your_browser_sdk_key
MATTERPORT_API_KEY=your_model_api_key
MATTERPORT_API_TOKEN=your_model_api_token
VEXCEL_API_KEY=your_aerial_api_key
```

Then, from `app/`, generate the ignored scan graphs and install the shared viewer bundle:

```sh
npm run setup:princeton
npm run setup:los-angeles
npm run dev:real
```

Both setup commands require network access. Princeton setup downloads the shared Matterport viewer and its scan graph; Los Angeles setup requires the Model API credentials and generates its own graph. The SDK key enables the viewer, and the Vexcel key enables aerial imagery. Open **http://localhost:4175/live.html#los-angeles** or **http://localhost:4175/live.html#princeton**; the sponsor SDK key is scoped to `localhost`. See [spatial setup](SPATIAL-ROUTES.md) for details. After restoring missing assets, reopen the demo to retry route initialization.

For catalog review without spatial demos, once the catalog is present, run the local server from `app/`:

```sh
npm run dev:real
```

Open http://127.0.0.1:4175. This server is loopback-only. `PORT=4180 npm run dev:real` selects another port. The earlier fixture demo uses `npm run dev` and port 4173; it is not the real-data service.

## Working demonstration

1. Open the Los Angeles demo at `/live.html#los-angeles`: 3160 Geneva St (CoStar 5956257), with Vexcel aerial imagery and the verified Matterport lobby scan. Search and city filters cover 3,339 properties.
2. Inspect the CoStar record. Interior references are original cached Matterport images joined by explicit property ID and consistent street number/name. They are not caller matches.
3. Start an incident with text and optionally a PNG/JPEG/WebP photo up to 2 MB. Use clearly marked demo text and an authorized image.
4. Open the caller view in another browser on this computer. Exchange messages or add a photo. Both views share disk-backed state, not browser-local state.
5. Edit the caller location, access instructions, notes, and unknowns. These are explicitly manual review fields. Import `packet.md` when available; HTML is escaped and claims still require review.
6. Reject with a reason, edit to reopen review, and approve. Approval is tied to the exact revision. Any subsequent edit, photo, or message requires a new approval.
7. Open the approved packet in a separate browser. Its snapshot remains unchanged after later incident edits. Copy its local link or export self-contained HTML with embedded images, Markdown, or Print/PDF.

Generate PDF and portable HTML exports from the packet view. Long notes, many unknowns, or a long imported packet can require more pages; content is not silently truncated.

## What is complete

- Searchable real CoStar catalog and source-record inspection.
- Real cached interior reference imagery on 22 properties (126 images).
- Photo intake and a local caller/dispatcher channel that works across browser contexts.
- Persistent incidents, manual review, safe Markdown import, and an actual local activity log.
- Revision conflict detection, rejection, edits, and immutable approved packet snapshots.
- Printable packet, Markdown, self-contained HTML, and local snapshot links.
- Optional offline-system-voice brief when the browser exposes a local English voice; no network voice provider.
- Responsive console and packet.

## What still depends on people or other lanes

No automatic caller localization, route computation, Vexcel aerial analysis, deep-agent events, Photon SMS/voice, external delivery, measured model accuracy, or interview result is fabricated. A/B/C provide these outputs and their supporting evidence. The approved local packet is not a dispatch instruction for actual emergency operations.

Agent orchestration, Photon delivery, and team submission materials are outside this standalone contribution.

## Local service contract

See `LOCAL-CONTRACT.md` for endpoints, payloads, revision handling, and the integration seam. `BACKEND.md` describes the earlier fixture adapter; it is not the implementation of this service.

Incidents and approved snapshots are JSON files in `.local-state/`, excluded from git. They survive browser refresh and service restart. Only one service process should own that directory. Stop the server before backing up or clearing it. Use a separate state directory in automated tests; the HTTP tests do this automatically.

The source data and cached assets are local sponsor material. No credentials or signed image URLs are put into the browser bundle. Their provenance and data limits are in `DATA-PROVENANCE.md`. The data builder is repeatable and offline:

```sh
python3 scripts/build-catalog.py --source /absolute/path/to/.local-data
```

## Validation

```sh
npm run check:real
npm test
```

35 tests cover fixture persistence, local-service revisions and snapshots, uploads, restricted serving, safe Markdown and URLs, route constraints, aerial requests, and retrying partial demo initialization. Tests create temporary fixture data and do not require credentials or sponsor downloads.

Live provider calls and SDK rendering require the local setup above; the offline suite does not establish provider availability.

### Vexcel aerial imagery

The hackathon quickstart specifies Vexcel `POST https://api.vexcelgroup.com/v2/ortho/extract` with longitude-first EPSG:4326 WKT. Set `VEXCEL_API_KEY` in the server environment or `app/.env.local`; it is never returned to the browser. `GET /api/aerial/<property-id>.jpg` extracts a 140 m square around a catalog property in Los Angeles or Houston. The default demo is 3160 Geneva St in Los Angeles (`/live.html#los-angeles`), with its verified Matterport lobby scan and matching aerial imagery. The sponsor expanded coverage to Princeton on November 14, so its existing route demo also requests Vexcel imagery.

Source: https://beta-app.matterport.com/2026-princeton-hackathon/quickstart (section 7). Live Vexcel requests require the sponsor key.
