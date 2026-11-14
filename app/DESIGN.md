---
name: "Pinpoint"
description: "Safety wayfinding for dispatcher review and responder packets."
colors:
  canvas: "#f4f6f7"
  surface: "#ffffff"
  ink: "#182026"
  muted: "#59636d"
  border: "#dce0e4"
  control-border: "#a6b0b8"
  subtle: "#edf0f2"
  red: "#c72535"
  red-hover: "#a71927"
  red-soft: "#fff1f2"
  red-ink: "#a31d2b"
  navy: "#294960"
  warning: "#73520e"
  warning-bg: "#fff8e8"
  success: "#286347"
  success-bg: "#edf7f1"
  focus: "#294960"
typography:
  display:
    fontSize: "3rem"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.035em"
    fontFamily: "Public Sans, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
  headline:
    fontSize: "2rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.025em"
    fontFamily: "Public Sans, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
  title:
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.025em"
    fontFamily: "Public Sans, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
  section:
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.025em"
    fontFamily: "Public Sans, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
  body-large:
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFamily: "Public Sans, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
  body:
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFamily: "Public Sans, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
  label:
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: 1.25
    fontFamily: "Public Sans, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
  metadata:
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFamily: "Public Sans, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
rounded:
  control: "4px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "20px"
  "6": "24px"
  "8": "32px"
  "10": "40px"
  "12": "48px"
  "16": "64px"
components:
  button-primary:
    backgroundColor: "{colors.red}"
    textColor: "{colors.surface}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "12px 16px"
  button-primary-hover:
    backgroundColor: "{colors.red-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "12px 16px"
  button-quiet:
    textColor: "{colors.red-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "12px 16px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "12px"
  navigation:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    height: "72px"
  status-review:
    backgroundColor: "{colors.warning-bg}"
    textColor: "{colors.warning}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
  working-pane:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "24px"
  destination:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    padding: "24px 32px"
---

# Design System: Pinpoint

## Overview

**Creative North Star: "Safety wayfinding"**

Pinpoint uses public-safety signage as its reference: a red location mark, clear Public Sans, and direct operational labels. A pale gray canvas holds white working panes. Red identifies meaningful actions, active selections, and the demo’s floor sign; conversation stays neutral.

Evidence, uncertainty, and review decisions remain visually distinct. Photography documents a source or caller observation rather than decorating the workspace. The same identity supports the live-data workspace and the separate synthetic demonstration.

**Key Characteristics:**

- One heading and body family, with a fixed seven-size ramp.
- Four-point spacing and consistent rectangular controls.
- Flat working panes, readable boundaries, and visible keyboard focus.
- Labeled evidence, explicit review states, and local-delivery limits.

## Colors

The palette keeps Pinpoint red and white over a restrained slate-gray foundation. The frontmatter contains the normative values.

### Primary

- **Action Red** (`red`, `red-hover`): primary actions and active navigation rules.
- **Readable Red** (`red-ink`, `red-soft`): selected records, rejection, and quiet destructive actions.

### Secondary

- **Route Navy** (`navy`, `focus`): route distinction and keyboard focus.
- **Review Amber** (`warning`, `warning-bg`): missing confirmation and pending review.
- **Approval Green** (`success`, `success-bg`): approved status and saved snapshots.

### Neutral

- **Canvas Gray** (`canvas`): page background and quiet message surfaces.
- **White** (`surface`): working panes, fields, and secondary controls.
- **Deep Ink** (`ink`): primary text and selected route profiles.
- **Muted Slate** (`muted`): secondary descriptions and metadata.
- **Divider Gray** (`border`): section and pane boundaries; **Control Gray** (`control-border`) gives fields and secondary controls stronger edges.
- **Soft Gray** (`subtle`): hover and dispatcher-message backgrounds.

**The Meaningful Color Rule.** Pair state color with text or shape; reserve red for actions, selection, destination signage, and rejection.

## Typography

**Display and Body Font:** locally bundled variable Public Sans with platform sans-serif fallbacks. Structured source payloads may use SFMono-Regular, Consolas, monospace; this is an inspection format, not a second UI family.

The fixed screen ramp is (12, 14, 16, 20, 24, 32, 48px) at the default root size, expressed in rem. Body, label, and heading weights are (400, 600, 700). Body leading is (1.5); ordinary heading leading is (1.25).

- **Display:** prominent demo floor numeral, with tabular figures and compact leading.
- **Headline:** page and destination headings; reduces to the title size on small screens.
- **Title:** selected building names and brand text.
- **Section:** major section headings.
- **Body large:** introductory text, subsection headings, facts, and mobile input text.
- **Body:** default interface and conversation text.
- **Label:** controls and concise action labels.
- **Metadata:** source descriptions, captions, timestamps, and supporting labels.

Live paragraphs use a maximum measure (75ch). Diagram labels and paper typography have their own context-specific constraints.

**The Task Heading Rule.** Use sentence-case headings that name the information or action immediately below them.

## Layout

Use the four-point rhythm in the frontmatter. Common relationships use (8–16px), pane insets use (20–24px), and major sections use (24–40px). Thin borders do not count as spacing steps. Screen composition is controlled by the shared sheet loaded after each surface’s base stylesheet.

The live workspace has a maximum width (1536px), desktop page gutters (32px), and a white navigation bar (72px). Its catalog joins search and detail in a bordered pane at a (0.9fr / 1.1fr) ratio. Incident work uses a flexible main pane and an adjacent conversation column (336px), with a gap (32px). Desktop conversation is sticky at (96px) and its message list can scroll.

At (1180px), the conversation narrows to (288px), pane insets become (20px), and search filters stack. At (800px), the live catalog and incident workspace become one column, page gutters become (16px), navigation wraps into a horizontally scrollable row, and conversation returns to natural document flow. The demo’s console stacks at (760px). At (480px), search controls stack and action groups use available width. Base styles retain intermediate evidence, packet, and demo layout adjustments; these are surface constraints, not additional spacing scales.

The live packet is bounded at (980px), the caller view at (720px), and the demo workspace at (1600px). Print is deliberately separate from the screen normalization: live packets use A4 with (12mm) margins and a (9pt) body; the demo uses A4 with (10mm) margins. Smaller paper captions and red print rules are format adaptations, not screen tokens. The vendor-controlled 3D iframe has its own interface; apply this system to its surrounding toolbar, fields, and status text.

## Elevation & Depth

Working panes use white surfaces, gray surroundings, and thin borders. They do not use card shadows. Dialogs and toast feedback share the overlay shadow (`0 16px 48px #18202626`); dialog backdrops use translucent ink (`#18202666`).

**The Flat Workspace Rule.** Use spacing, surface tone, and borders for working areas; reserve shadows for transient overlays.

## Shapes

Controls, working panes, messages, segmented frames, dialogs, and feedback share the control radius (4px). List rows join with square edges and dividing rules. State dots, route markers, and loading spinners remain circular because their shapes communicate state or position.

## Components

### Buttons

Primary actions use red with white text; hover deepens the red. Secondary actions use white, ink text, and the control border; hover uses soft gray. The demo also has quiet red rejection controls. Standard controls have a minimum height (44px), medium-weight labels, and the frontmatter inset. Compact spatial selectors use (40px). Disabled buttons reduce opacity to (0.5). Busy actions retain their original accessible label and dimensions while a centered spinner replaces the visible label.

### Inputs / Fields

White fields use the control border and consistent radius. Live fields have a minimum height (44px), an inset (12px), and explicit labels. Textareas resize vertically; the conversation composer is at least (96px) tall. Mobile input text uses the body-large size. The shared focus treatment is a navy outline (2px) with offset (4px); spatial selects and demo composer frames retain their local focus rules.

### Navigation and selection

Navigation uses muted text and a red bottom rule for the current page. Selected property rows use the red wash and readable-red title, with an inset focus outline. Route profile controls use an ink-filled selected segment and white text. Selection is expressed through `aria-current` or `aria-pressed`, not color alone.

### Working panes and destination

Catalog, incident, packet, and caller surfaces use white panes with the shared radius. The screen destination strip is neutral with dark text and muted context. The synthetic demo adds a compact red floor sign; the whole strip is not a red banner. Status uses labeled amber, green, or red treatment according to review state.

### Evidence, review, and conversation

Images retain their proportions and carry provider-specific source or caller labels. Satellite approach keeps its projected map proportions at every viewport size; its key and attribution sit outside the image. Route labels scale to remain at least (14px) on screen. Unknown facts and dispatcher notes precede approval. Caller and dispatcher messages use neutral surfaces with role and time labels. Desktop conversation sits beside incident work; mobile keeps the full thread in page flow. Supporting source records and activity use disclosure controls.

### Feedback and motion

Control color changes use the fast transition (160ms ease-out). Loading uses a structural skeleton and a status label; skeleton pulse lasts (1.4s), and busy-button rotation lasts (0.8s). Reduced-motion preferences remove animation and transitions. Empty states explain the next useful action, request failures remain readable, and mutation errors preserve entered text. Toast feedback uses a live region; form failures use an alert. These states communicate work or recovery rather than decorate page arrival.

## Do's and Don'ts

### Do:

- **Do** use the shared tokens for screen spacing, typography, and control shapes.
- **Do** preserve source labels, uncertainty, review status, and local-only delivery language.
- **Do** keep keyboard focus visible and interactive states understandable without color.
- **Do** preserve image proportions and provide useful empty, loading, and error states.

### Don't:

- **Don't** add decorative colors, extra heading fonts, or arbitrary screen type sizes.
- **Don't** turn conversation messages into primary-action red surfaces.
- **Don't** use dialog shadows on ordinary working panes.
- **Don't** imply verification, emergency dispatch, or external delivery from a local approval.

Not canonized: vendor-controlled viewer styling, contextual SVG geometry, and compact print typography.
