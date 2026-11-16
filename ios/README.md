# Pinpoint for iPhone

Swift + SwiftUI responder demo, with MapKit outdoors and the existing Matterport Bundle SDK in a WKWebView indoors. Requires iOS 17+. The app hosts its bundled viewer on loopback; it does not need the dispatcher server or collect GPS location. Internet is required for Apple Maps and Matterport scan imagery.

## Put it on your phone

1. Open `Pinpoint.xcodeproj` in Xcode.
2. In Xcode Settings → Apple Accounts, sign in with your Apple Account if needed.
3. Select the **Pinpoint** target → **Signing & Capabilities**. Keep automatic signing enabled and choose your team.
4. If the bundle identifier is unavailable, replace `com.pinpoint.responder.demo` with a unique identifier.
5. Connect and unlock your iPhone. Select it as the run destination, enable Developer Mode if prompted, and press **Run** (⌘R).

Apple references: [Run on a device](https://help.apple.com/xcode/mac/current/en.lproj/dev5a825a1ca.html), [Developer Mode](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device).

## Demo walkthrough

1. **Accept & start demo**, then **Advance to approach**.
2. **Enter building** loads the real scan at 12814 Willow Centre Dr, Houston.
3. **Dollhouse** shows the building with the red route embedded in the 3D scene. Drag/pinch to explore.
4. **Start walkthrough** switches to **First person** at reception, facing the route.
5. **Next waypoint** moves through ten captured positions toward the conference room. The back arrow beside the primary action returns to the previous waypoint.
6. Switch **Dollhouse / First person** at any point without reloading the scan or losing route progress.
7. At waypoint 10, **Mark caller reached** completes the sample incident. **Run demo again** resets it.

The document button opens the incident brief. Offline or viewer failures show **Retry scan**. Route arrows are withheld if scan positions do not align with the bundled graph. The incident and traversal graph are illustrative; current access and suitability for emergency use are not verified.

## Building the viewer assets

The Xcode build phase runs `prepare-viewer.mjs` using Node.js (20+). It copies the existing SDK from `app/vendor/matterport`, generates a deterministic route from `app/data/10352309-scan.json`, and copies the same `routeComponentFactory` used by `app/src/live-spatial.js` into the app bundle.

The browser SDK key is read from `MATTERPORT_SDK_KEY`, then `app/.env.local`, then the SDK-key entry in the repository's existing `api.txt`. Only the browser SDK key is bundled; server-side Matterport API and Vexcel credentials are never copied. Generated assets and key-bearing config are not committed. A checkout without the local model/SDK assets must run the repository's existing `python3 -m live.setup` workflow first. The key must allow `localhost`.

The app's asset server binds only to `127.0.0.1` on a dynamically assigned port, serves GET/HEAD within the bundled viewer directory, and rejects traversal paths. It exposes no dispatcher APIs.

## Checks

```sh
node --test ios/tests/viewer.test.mjs
xcodebuild -project ios/Pinpoint.xcodeproj -scheme Pinpoint \
  -sdk iphonesimulator -configuration Debug \
  -derivedDataPath /tmp/pinpoint-ios-build CODE_SIGNING_ALLOWED=NO build
```

Debug builds accept `-demo-stage 3` to open directly on the indoor view, and `-demo-brief` for the brief. These overrides are absent from Release builds.

## Verification and limits

The complete ten-position walkthrough, arrival, reset, and switching between both views were exercised in the iPhone 18 Pro simulator on iOS 27.2. Dark appearance and XXXL Dynamic Type were checked. Five automated bridge tests pass. Physical-device and VoiceOver operation still need testing.

The older iOS 18.1 simulator showed corrupt dollhouse textures and a viewer failure during a mode switch. Use the current simulator for the demo; a stalled view now exposes retry after 20 seconds. This does not establish whether that older-runtime graphics issue occurs on physical phones.

Manual exploration outside the highlighted route shows **Return to route** and cannot claim arrival based on the last visited waypoint.


### SwiftUI Expert + Impeccable refinement

The route panel now shows waypoint progress and camera movement status. Accessibility text sizes use scrollable details with a fixed, reachable action row. Restart stops the indoor viewer and resets its state; it loads again only when entering the building. Stale viewer callbacks are ignored after a reset. Debug-only web inspection is disabled in Release.

This pass verified default/light and largest accessibility text/dark layouts, detail scrolling, walkthrough entry, forward/back movement, and restart/re-entry on iPhone 18 Pro / iOS 27.2. Simulator and unsigned device builds pass; five bridge tests pass. The existing physical-device and VoiceOver testing limits still apply.
