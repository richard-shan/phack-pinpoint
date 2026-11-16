# Restart checkpoint — November 15, 2026

## Saved state

- iOS app source is in `ios/Pinpoint`; project is `ios/Pinpoint.xcodeproj`.
- Removed the top-bar `SIMULATED DEMO` label and its accessibility-size `DEMO` variant from `PinpointApp.swift`.
- The Debug simulator build succeeded after this edit.
- Installed and launched `com.pinpoint.responder.demo` on the iPhone 18 Pro simulator (iOS 27.2), device ID `789069A1-CE34-457A-99AE-9264CCFD923D`.
- This Xcode setup uses Device Hub to display the simulator. `open -a Simulator` did not work; `open -a "Device Hub"` did.
- The entire `ios/` directory currently exists as untracked files on Git branch `main`. Files are saved on disk, but no Git commit or remote backup was made.
- Other existing untracked directories/files were left untouched: `.agents/`, `.codex/`, `.playwright-cli/`, `CLAUDE.local.md`, and `output/`.

## Resume after restarting the Mac

Open `ios/Pinpoint.xcodeproj` in Xcode, select the Pinpoint scheme and iPhone 18 Pro simulator, then Run. Open Device Hub to view it.

To build from a terminal in the repository root:

```sh
xcodebuild -project ios/Pinpoint.xcodeproj -scheme Pinpoint \
  -sdk iphonesimulator -configuration Debug \
  -derivedDataPath /tmp/pinpoint-ios-build CODE_SIGNING_ALLOWED=NO build
```

After booting the iPhone 18 Pro simulator in Device Hub:

```sh
xcrun simctl install 789069A1-CE34-457A-99AE-9264CCFD923D /tmp/pinpoint-ios-build/Build/Products/Debug-iphonesimulator/Pinpoint.app
xcrun simctl launch 789069A1-CE34-457A-99AE-9264CCFD923D com.pinpoint.responder.demo
open -a "Device Hub"
```

Build output and the last build log (`/tmp/pinpoint-rebuild.log`) are temporary; rebuild if they are missing after restart. The app bundles its viewer and does not require the local dispatcher server. Internet is required for map and Matterport imagery. See `ios/README.md` for the full walkthrough and setup.

## Photon context

Reviewed the current Photon implementation earlier in this chat; no Photon code was changed. `live/photon.py` receives Socket.IO events, adapts them to the shared bridge in `live/telegram.py`, and sends replies through REST. The default approved packet delivery is a PDF in the original conversation. `live/PHOTON.md` contains setup and previous live verification notes.
