# Pinpoint native responder extension

Inherits the safety-wayfinding identity in `../app/DESIGN.md`; the web design system is unchanged.

- **Mode:** Operate. The map owns the screen; safe-area panels carry the immediate instruction and incident action.
- **Typography:** native SF system text styles, Dynamic Type, headline actions, secondary subheadlines, caption metadata.
- **Action/destination:** Pinpoint red, RGB 0.78 / 0.145 / 0.21.
- **Route:** RGB 0.16 / 0.36 / 0.52 in light appearance; RGB 0.38 / 0.69 / 0.90 in dark appearance for visibility.
- **Surfaces:** semantic system grouped backgrounds and native regular material; primary and secondary semantic text.
- **Spacing:** 12 pt related groups, 16 pt screen inset/instruction padding, 20 pt incident panel padding.
- **Shape:** 16 pt instruction panel and 20 pt incident panel; circular map controls. Native adaptation of the existing flat web working panes.
- **Controls:** SF Symbols, native sheet/List/Menu/Map, 44–48 pt utility targets, full-width primary action. No custom navigation stack.
- **Motion:** short stage transition; respects Reduce Motion.
- **Maps:** street map is interactive; indoor Matterport scan uses the central safe region. A native segmented control selects Dollhouse or First person, and native buttons step through captured positions with red arrows anchored to the scan floor. Route/incident content is illustrative; the building scan is real.

## Review

The initial native map/schematic demo passed review. The 3D extension preserves these tokens and the web design system; its review and interaction results are recorded in `.impeccable/review/3d-verification.md`. Matterport runtime assets retain their vendor license. No generated artwork is used.

## SwiftUI Expert refinement

The indoor panel groups the destination, waypoint progress, guidance status, previous control, and primary action. The progress bar measures captured waypoint traversal, not physical distance. Pending camera moves show an explicit moving state. Full incident metadata stays in the brief.

At accessibility text sizes, header text simplifies and detail sections reflow vertically into a bounded scroll region. Primary controls stay outside that scroll region. The panel uses 58% of the available safe-area height at those sizes so a usable map remains visible; default text sizes retain content-driven height. Short visible action labels retain their full accessibility names. Map annotation labels describe the entrance and simulated responder position.

Verification for this pass: `.impeccable/review/swiftui-polish.md`.
