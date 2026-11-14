# Princeton spatial routing

Open `http://localhost:4175/live.html#princeton` after `npm run dev:real`. The sponsor key currently allows `localhost`; use that hostname instead of `127.0.0.1`.

The live console uses the public Rabinowitz Hall model `P3S6VUTP3qB`, local Matterport Bundle SDK 26.10.4, and Vexcel aerial imagery. `app/.env.local` holds `MATTERPORT_SDK_KEY` and is ignored by Git. This is a browser SDK key, delivered only by the loopback configuration endpoint; no GraphQL token or sponsor data credentials are shipped to the page. The downloaded SDK is under `vendor/matterport/`, also ignored. Both the key and downloaded model data are local event material.

## What is registered

`data/princeton-scan.json` contains the 66 captured puck positions, their floor IDs, explicit demo corridor edges, and visually checked main stair flights (35→36→37). Source Z-up puck positions are converted to SDK Y-up as `(x, z, -y)`. The renderer checks all available live SDK pucks against that transform and hides the overlay if it differs by more than 30 cm. Scene meshes sit above the floor and participate in the viewer's perspective and depth testing; they are not screen overlays. Each route replacement disposes the old meshes. Switching views, messages, polling, and review updates keep the iframe mounted.

The aerial approach uses geographic waypoints ending at the selected entrance anchor. An entry segment joins the corresponding world-space anchor to the first captured scan. **The exterior anchors and path are illustrative, not surveyed, and the scan begins inside the corridor.** The public scan does not establish the complete path from the street through an exterior doorway. Verify/calibrate those coordinates before describing this as a measured door-to-door route.

The stair flights are visible in the source panoramas. There is no verified elevator connection in the supplied scan. Step-free routing excludes stairs and reports no route until a dispatcher confirms and maps an elevator using the connection controls, or chooses a same-level destination. Mapped elevator connections are explicitly dispatcher-provided, with a vertical segment between landings. There is no claim that route geometry proves accessibility, door availability, or emergency suitability.

## Agent updates

`POST /api/incidents/:id/route` recomputes and persists the route. Supply the current incident revision from `GET /api/incidents/:id`. Example:

```json
{
  "expectedRevision": 2,
  "profile": "foot",
  "entryId": "northwest",
  "destination": "ih6tueuzaxnyw3f47m5ygeccd",
  "blockedEdges": [],
  "updatedBy": "agent",
  "reason": "Southwest approach unavailable; use the northwest approach."
}
```

The response is the revised incident, including `spatialRoute`. It invalidates any prior approval, writes an activity event, and updates open consoles within 1.5 seconds. A stale revision returns 409. Existing caller drafts stay intact. No external agent runner is configured in this repository; the endpoint is the integration boundary for it.

Optional `nodeIds` lets the agent supply its determined path instead of requesting shortest-path calculation. Every transition must exist in the traversable graph, respect blocked segments and profile constraints, start at the selected entry scan, and end at the destination. Unknown/unconnected paths are rejected. The graph deliberately does not use all Matterport panorama neighbors, because those include sightlines across open space.

Optional `connectors` is an array of `{a, b, kind: "elevator"|"stairs", confirmed: true}` using captured sweep IDs. Geometry remains tied to actual scan positions. A no-route result is persisted with empty geometry, clearing stale paths. Approved packet snapshots retain the route and revision they approved.

## Verification

Run `npm test`, `npm run check`, and `npm run check:real`. The behavioral tests cover entry continuity, stairs, confirmed elevators, step-free exclusion, blockers, disconnected agent paths, concurrent revision rules, and immutable packets. Live browser checks additionally exercise SDK initialization, actual floor registration, view/floor controls, route updates, and satellite loading.

Matterport references: https://developers.matterport.com/bundle-sdk/ and https://developers.matterport.com/reference/sweep/.

## Local assets and CSP

The runtime bundle can be restored from the official archive at `https://static.matterport.com/showcase-sdk/bundle/26.10.4_webgl-1728-g1cd7e0997a/showcase-bundle.zip`; extract it into `app/vendor/matterport/` so that `showcase.html` is directly inside that directory. Run `npm run setup:princeton` to restore the bundle and regenerate the local model data (requires network access). Keep `data/princeton-scan.json` with the local demo: it includes the model-specific traversal annotations and is intentionally excluded from Git. The bundle's browser check uses JavaScript eval, and its Troika worker imports blob scripts. Only the vendor viewer responses receive the broader CSP needed for those operations; the dispatcher document retains its stricter policy. Aerial imagery uses Vexcel, the hackathon sponsor. The sponsor expanded coverage to Princeton on November 14; the Princeton route view now uses Vexcel as well.

## Los Angeles route demo

`/live.html#los-angeles` uses the 3160 Geneva Street lobby model (`JUz7H4u8m5C`), with 23 real floor anchors and 22 illustrative, same-level segments. Regenerate the local graph with `npm run setup:los-angeles` after setting server-side `MATTERPORT_API_KEY` and `MATTERPORT_API_TOKEN` in `.env.local`. The graph is excluded from Git with the other local sponsor assets.

The shared route viewer loads configuration by property ID; route updates choose the graph from the saved incident, never from a client-supplied model ID. Destination selection, segment blocking, floor display, walkthrough and approval revision behavior match the Princeton demo. Coordinates use Model API `anchor.position`, transformed `(x,z,-y)` into SDK space. SDK floor-puck alignment is checked before displaying geometry.

The selected same-floor scan chains are demonstration annotations, not proof of walkable or accessible paths. The Vexcel exterior approach is illustrative and is not surveyed or registered to the indoor doorway. Step-free mode excludes mapped stairs; it does not certify door clearance or current access.
