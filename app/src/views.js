import { agentSteps } from "./fixtures.js";

export const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
  );
const e = escapeHtml;
const icons = {
  zoomIn: '<path d="M5 12h14M12 5v14"/>',
  zoomOut: '<path d="M5 12h14"/>',
  target:
    '<circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="1.5"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  up: '<path d="m7 17 10-10M7 7h10v10"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  pin: '<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2"/>',
  building:
    '<path d="M4 21V3h12v18M16 10h4v11M2 21h20M8 7h4M8 11h4M8 15h4M8 21v-3h4v3"/>',
  image:
    '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  file: '<path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8M8 16h6"/>',
  route:
    '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h9a4 4 0 0 1 0 8H8a4 4 0 0 0 0 8h7"/>',
  foot: '<circle cx="13" cy="4" r="2"/><path d="m7 21 3-7 4 3v4m-4-7 2-7 4 4h4M6 11l2-4h4"/>',
  stretcher:
    '<path d="M3 15h18M5 15v4m14-4v4M4 11h7l3 4M3 6v9m18-5v5M8 6v5M6 8h4"/><circle cx="5" cy="20" r="1"/><circle cx="19" cy="20" r="1"/>',
  play: '<path d="m8 4 12 8-12 8Z"/>',
  reset: '<path d="M3 10a9 9 0 1 1 1 8M3 4v6h6"/>',
  link: '<path d="m9 15 6-6M7 14l-2 2a4 4 0 0 0 6 5l3-3M10 6l3-3a4 4 0 1 1 6 5l-2 2"/>',
  edit: '<path d="m14 5 5 5M4 20l5-1L21 7l-5-5L4 14Z"/>',
  shield:
    '<path d="m12 2 8 4v6c0 6-8 10-8 10S4 18 4 12V6Z"/><path d="m8 12 3 3 5-6"/>',
  alert: '<path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3v1"/>',
  print: '<path d="M6 8V3h12v5M6 17H3V8h18v9h-3M6 14h12v7H6Z"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  layers: '<path d="m12 3 10 6-10 6L2 9Zm-10 11 10 6 10-6M2 18l10 5 10-5"/>',
};
export const icon = (name, className = "") =>
  `<svg class="icon ${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.file}</svg>`;
export const sourceButton = (id, label) =>
  `<button class="source-link" data-action="evidence" data-id="${e(id)}">${e(label)}${icon("up")}</button>`;
const statusNames = {
  review: "Awaiting approval",
  running: "Preparing packet",
  approved: "Approved locally",
  rejected: "Revision requested",
};
export const statusBadge = (status) =>
  `<span class="status-badge ${e(status)}"><span class="status-dot"></span>${statusNames[status] || e(status)}</span>`;

function header(packet = false) {
  return `<header class="topbar"><a href="#console" class="brand" aria-label="Pinpoint dispatcher console"><img src="/assets/mark.svg" alt="" width="30" height="30"/>Pinpoint</a><nav class="top-nav" aria-label="Workspace"><a href="#console" ${!packet ? 'aria-current="page"' : ""}>Dispatch</a><a href="#packet/PP-1042" ${packet ? 'aria-current="page"' : ""}>Responder packet</a><a href="http://localhost:4175/live.html#princeton">Princeton 3D demo</a></nav><span class="local-indicator">Local demo</span></header>`;
}
function destination(incident, document = false) {
  const ready = incident.status !== "running";
  return `<section class="destination" aria-label="Caller destination"><div class="floor-sign" aria-label="Floor ${incident.location.floor}"><strong>${ready ? incident.location.floor : "—"}</strong><span>Floor</span></div><div class="destination-name"><h1>${ready ? e(incident.location.room) : "Locating the caller"}</h1><p>${ready ? `Suite ${e(incident.location.suite)} · ` : ""}${e(incident.building.name)}</p><p class="destination-address">${e(incident.building.address)}</p></div><div class="incident-meta"><strong>${document ? "Responder packet" : e(incident.type)}</strong><span>${e(incident.id)} · ${e(incident.priority)}</span>${ready ? `<button data-action="evidence" data-id="location" class="destination-source">${Math.round(incident.location.confidence * 100)}% demo match ${icon("up")}</button>` : "<span>Fixture replay in progress</span>"}</div></section>`;
}
function profiles(profile, name = "Route profile") {
  return `<div class="segmented" aria-label="${name}">${[
    ["foot", "On foot"],
    ["stretcher", "Stretcher"],
  ]
    .map(
      ([key, label]) =>
        `<button data-action="profile" data-value="${key}" aria-pressed="${profile === key}" class="${profile === key ? "selected" : ""}">${icon(key)}${label}</button>`,
    )
    .join("")}</div>`;
}
function thread(incident) {
  return `<aside class="caller-panel" aria-labelledby="caller-heading"><div class="caller-header"><h2 id="caller-heading" tabindex="-1">Caller conversation</h2><span>Simulated</span></div><div class="messages" aria-label="Caller conversation" tabindex="0">${incident.messages.map((message) => `<div class="message ${e(message.role)}"><div class="message-label"><strong>${message.role === "caller" ? "Caller" : message.role === "agent" ? "Pinpoint" : "Dispatcher"}</strong><time>${e(message.time)}</time></div><div class="bubble">${e(message.text)}${message.image ? `<button class="photo-button" data-action="photo" aria-label="View caller photo"><img src="${e(incident.callerPhoto)}" alt="${incident.photoIsUpload ? "Locally uploaded caller photo" : "Synthetic elevator lobby with blue suite sign"}"/><span>${incident.photoIsUpload ? "Local upload · not processed" : "Synthetic demo photo"} ${icon("up")}</span></button>` : ""}</div></div>`).join("")}</div>${incident.awaitingCaller ? `<div class="caller-wait"><strong>Caller confirmation needed</strong><button class="button primary" data-action="reply">Simulate “yes” reply ${icon("arrow")}</button></div>` : ""}<form id="message-form" class="composer"><label class="sr-only" for="message">Message caller</label><div class="composer-input"><input id="message" name="message" maxlength="1000" autocomplete="off" placeholder="Message caller…" required/><button class="icon-button send-button" type="submit" aria-label="Send demo message">${icon("arrow")}</button></div><span>No message leaves this demo.</span></form><button class="upload-button" data-action="upload">${icon("upload")}Try a caller photo <span>2 MB max</span></button><input type="file" id="photo-input" accept="image/png,image/jpeg,image/webp" hidden/></aside>`;
}
export function floorMap(profile = "foot", zoom = false) {
  const foot = profile === "foot";
  return `<svg class="floor-map ${zoom ? "map-zoomed" : ""}" viewBox="0 0 560 390" role="img" aria-label="Floor 2 east wing. Caller beside Suite 204. ${foot ? "On-foot route via Stair B" : "Stretcher route via Elevator A"}. Schematic, not to scale."><g class="floor-content"><path d="M32 55H528V327H32Z" fill="#fff" stroke="#8a959b" stroke-width="3"/><path d="M33 163H527V228H33Z" fill="#e8edef"/><path d="M32 163H238V55M32 228H238V327M342 228V327M238 163H528M342 55V163" fill="none" stroke="#a3adb2" stroke-width="2"/><path d="M343 56H527V162H343Z" fill="#fff0f1"/><g font-family="Public Sans, sans-serif" text-anchor="middle"><text x="135" y="116" fill="#53616a" font-size="17">Offices</text><text x="290" y="104" fill="#53616a" font-size="15">Suite</text><text x="290" y="128" fill="#53616a" font-size="19">203</text><text x="435" y="116" fill="#a31d2b" font-size="23" font-weight="750">Suite 204</text><text x="135" y="280" fill="#53616a" font-size="17">Reception</text><text x="290" y="307" fill="#263c4c" font-size="18" font-weight="650">Elevator A</text><text x="435" y="307" fill="#263c4c" font-size="18" font-weight="650">Stair B</text></g><path d="M269 228h42m103 0h42" stroke="#e8edef" stroke-width="6"/><rect x="264" y="255" width="52" height="26" fill="#edf0f2" stroke="#8a959b"/><path d="M290 255v26m-19-11 5-6 5 6m18-6 5 6 5-6" stroke="#53616a" fill="none"/><path d="M409 281h52m-52-6h52m-52-6h52m-52-6h52m-52-6h52" stroke="#8a959b" stroke-width="2"/><path d="${foot ? "M435 247V195H372" : "M290 247V195H372"}" stroke="${foot ? "#c72535" : "#294960"}" stroke-width="6" stroke-linejoin="round" fill="none" stroke-dasharray="${foot ? "none" : "9 6"}"/><circle cx="372" cy="195" r="16" fill="#f7c6cd"/><circle cx="372" cy="195" r="8" fill="#c72535" stroke="#fff" stroke-width="3"/><path d="M372 181v-32" stroke="#a31d2b" stroke-width="1.5"/><rect x="339" y="130" width="66" height="24" fill="#c72535"/><text x="372" y="147" fill="#fff" text-anchor="middle" font-family="Public Sans, sans-serif" font-weight="650" font-size="14">Caller</text><text x="32" y="365" fill="#53616a" font-family="Public Sans, sans-serif" font-size="14">East wing · Floor 2</text></g></svg>` + compactFloorMap(profile, zoom);
}
export function siteMap(profile = "foot") {
  const foot = profile === "foot";
  return `<svg class="site-map" viewBox="0 0 560 390" role="img" aria-label="Synthetic site diagram. ${foot ? "East loading bay to entrance E-02" : "South drop-off to accessible entrance S-01"}. Not to scale."><rect width="560" height="390" fill="#f3f5f5"/><path d="M0 342H560M474 0V342" stroke="#d9e0e3" stroke-width="50"/><path d="M0 342H560" stroke="#fff" stroke-width="2" stroke-dasharray="15 10"/><rect x="126" y="59" width="280" height="224" fill="#fff" stroke="#8a959b" stroke-width="3"/><g font-family="Public Sans, sans-serif" fill="#263c4c" text-anchor="middle"><text x="266" y="153" font-size="23" font-weight="750">Westbridge</text><text x="266" y="181" font-size="18">Medical Center</text><text x="266" y="212" font-size="15">2 floors · synthetic site</text><text x="140" y="373" font-size="14">Demo Park Drive</text><text x="266" y="39" font-size="15">North · N-01</text></g><path d="${foot ? "M474 258V170H406" : "M266 323V283"}" stroke="${foot ? "#c72535" : "#294960"}" stroke-width="6" fill="none" stroke-dasharray="${foot ? "none" : "9 6"}"/><circle cx="406" cy="170" r="7" fill="${foot ? "#c72535" : "#74838d"}"/><circle cx="266" cy="283" r="7" fill="${foot ? "#74838d" : "#294960"}"/><g font-family="Public Sans, sans-serif" font-size="16" font-weight="700" fill="#263c4c"><text x="420" y="150">E-02</text><text x="282" y="309">S-01</text></g><text x="32" y="32" font-family="Public Sans, sans-serif" font-size="14" fill="#53616a">Site approach</text></svg>` + compactSiteMap(profile);
}
// Focused geometry keeps labels readable on phone-sized route and packet views.
function compactFloorMap(profile, zoom) {
  const foot = profile === "foot";
  return `<svg class="floor-map compact-map ${zoom ? "map-zoomed" : ""}" viewBox="0 0 360 300" role="img" aria-label="Floor 2. Caller beside Suite 204. ${foot ? "Route via Stair B" : "Route via Elevator A"}. Schematic, not to scale."><g class="floor-content" font-family="Public Sans, sans-serif"><path d="M12 30H348V254H12Z" fill="#fff" stroke="#8a959b" stroke-width="2"/><path d="M13 137H347V188H13Z" fill="#e8edef"/><path d="M230 31H347V136H230Z" fill="#fff0f1"/><path d="M145 30V137M230 30V137M12 137H348M12 188H348M145 188V254M230 188V254" fill="none" stroke="#a3adb2"/><g text-anchor="middle" fill="#53616a" font-size="18"><text x="78" y="89">Offices</text><text x="187" y="76">Suite</text><text x="187" y="99">203</text><text x="287" y="80" fill="#a31d2b" font-weight="750">Suite 204</text><text x="78" y="225">Reception</text><text x="187" y="223" fill="#263c4c" font-weight="650">Elevator</text><text x="187" y="245" fill="#263c4c" font-weight="650">A</text><text x="290" y="235" fill="#263c4c" font-weight="650">Stair B</text></g><path d="M177 188h20m83 0h20" stroke="#e8edef" stroke-width="5"/><path d="${foot ? "M290 208V162H250" : "M187 203V162H250"}" fill="none" stroke="${foot ? "#c72535" : "#294960"}" stroke-width="5" stroke-linejoin="round" stroke-dasharray="${foot ? "none" : "8 5"}"/><circle cx="250" cy="162" r="12" fill="#f7c6cd"/><circle cx="250" cy="162" r="6" fill="#c72535" stroke="#fff" stroke-width="2"/><path d="M250 150v-18" stroke="#a31d2b"/><rect x="218" y="106" width="64" height="27" fill="#c72535"/><text x="250" y="126" font-size="18" font-weight="650" fill="#fff" text-anchor="middle">Caller</text><text x="12" y="284" fill="#53616a" font-size="18">East wing · Floor 2</text></g></svg>`;
}
function compactSiteMap(profile) {
  const foot = profile === "foot";
  return `<svg class="site-map compact-map" viewBox="0 0 360 300" role="img" aria-label="Synthetic site. ${foot ? "East loading bay, door E-02" : "South drop-off, accessible door S-01"}. Not to scale."><rect width="360" height="300" fill="#f3f5f5"/><path d="M0 256H360M327 0V256" stroke="#d9e0e3" stroke-width="32"/><path d="M0 256H360" stroke="#fff" stroke-dasharray="10 8"/><rect x="38" y="46" width="224" height="170" fill="#fff" stroke="#8a959b" stroke-width="2"/><g font-family="Public Sans, sans-serif" fill="#263c4c" font-size="18" text-anchor="middle"><text x="150" y="28">North · N-01</text><text x="150" y="119" font-size="22" font-weight="750">Westbridge</text><text x="150" y="145">Medical Center</text><text x="150" y="176">2 floors · demo</text><text x="150" y="290">Demo Park Drive</text></g><path d="${foot ? "M327 215V138H262" : "M150 243V216"}" stroke="${foot ? "#c72535" : "#294960"}" stroke-width="5" stroke-dasharray="${foot ? "none" : "8 5"}" fill="none"/><circle cx="262" cy="138" r="6" fill="${foot ? "#c72535" : "#74838d"}"/><circle cx="150" cy="216" r="6" fill="${foot ? "#74838d" : "#294960"}"/><g font-family="Public Sans, sans-serif" font-size="18" font-weight="700" fill="#263c4c"><text x="272" y="124">E-02</text><text x="163" y="238">S-01</text></g></svg>`;
}
function mapPanel(incident, ui) {
  const ready = incident.status !== "running";
  return `<section class="map-panel" aria-label="Caller location and route"><div id="map-content" class="map-canvas ${!ready ? "map-pending" : ""}" role="tabpanel" aria-labelledby="tab-${ui.mapTab}">${ui.mapTab === "floor" ? floorMap(ui.profile, ui.zoom) : ui.mapTab === "site" ? siteMap(ui.profile) : `<div class="photo-comparison"><figure><img src="${e(incident.callerPhoto)}" alt="Caller photo ${incident.photoIsUpload ? "uploaded locally" : "synthetic fixture"}"/><figcaption>Caller photo · ${incident.photoIsUpload ? "Not processed" : "Synthetic"}</figcaption></figure><figure><img src="/assets/lobby.png" alt="Synthetic reference lobby"/><figcaption>Reference · Fixture L2-E07</figcaption></figure><p>${incident.photoIsUpload ? "Your photo is previewed locally. No image matching runs." : "The same synthetic image demonstrates the comparison. No image matching has run."}</p></div>`}${ui.mapTab === "floor" ? `<button class="map-zoom icon-button" data-action="zoom" aria-label="${ui.zoom ? "Reset map zoom" : "Zoom map"}" aria-pressed="${ui.zoom}">${icon(ui.zoom ? "zoomOut" : "zoomIn")}</button>` : ""}${!ready ? `<div class="map-processing" role="status"><div class="loading-bars" aria-hidden="true"><i></i><i></i><i></i></div><strong>${incident.awaitingCaller ? "Waiting for caller confirmation" : "Locating the caller"}</strong><span>${incident.awaitingCaller ? "Use the simulated reply in the caller conversation." : "Checking building, location, and route fixtures."}</span></div>` : ""}</div><div class="map-legend"><span><i class="legend-dot"></i>Caller</span><span><i class="legend-line ${ui.profile}"></i>${ui.profile === "foot" ? "On foot" : "Stretcher"}</span><span>Schematic · not to scale</span></div><div class="map-source">${sourceButton(ui.mapTab === "site" ? "site" : "location", ui.mapTab === "site" ? "Site evidence" : "Location evidence")}<span>All evidence is synthetic</span></div></section>`;
}
function directions(incident, profile) {
  const route = incident.routes[profile];
  return `<section class="directions" aria-label="Route instructions"><div class="entry-heading"><h2>${e(route.entrance)}</h2><p><strong>${e(route.door)}</strong> · ${e(route.staging)}</p>${sourceButton("site", "Entrance evidence")}</div><div class="route-measure"><span>${route.meters} m</span><span>${route.seconds} sec estimated</span></div><ol>${route.steps.map((step, index) => `<li><span class="route-number">${index + 1}</span><p>${e(step)}</p></li>`).join("")}</ol><div class="route-footnote">${sourceButton("route", route.vertical)}<span>Fixture estimates, not measured performance.</span></div></section>`;
}
function unknowns(incident) {
  return `<section class="unconfirmed"><h3>${icon("alert")}Not confirmed <span>${incident.packet.unconfirmed.length}</span></h3><ul>${incident.packet.unconfirmed.length ? incident.packet.unconfirmed.map((item) => `<li>${e(item)}</li>`).join("") : "<li>No items listed; this is not verification.</li>"}</ul></section>`;
}
function notes(incident) {
  return `<section class="dispatcher-notes"><h3>Dispatcher notes <span>Revision ${incident.revision}</span></h3><p>${e(incident.packet.notes)}</p></section>`;
}
function equipment() {
  return `<section class="equipment"><h3>Along the way ${sourceButton("equipment", "Source")}</h3><dl><div><dt>AED</dt><dd>East elevator lobby</dd></div><div><dt>Extinguisher</dt><dd>Beside Stair B</dd></div></dl></section>`;
}
function review(incident) {
  return `<section id="responder-packet" class="review-section" tabindex="-1" aria-labelledby="packet-heading"><div class="review-heading"><h2 id="packet-heading">Packet review</h2><a class="text-button" href="#packet/${e(incident.id)}">Open full packet ${icon("up")}</a></div>${incident.status === "running" ? '<div class="packet-waiting"><h3>Preparing the responder packet</h3><p>Complete the simulated caller conversation to continue.</p></div>' : `<div class="review-columns">${unknowns(incident)}${notes(incident)}</div>${equipment()}`}${incident.status === "rejected" ? `<div class="rejection-reason"><strong>Revision requested</strong><p>${e(incident.decision?.reason)}</p></div>` : ""}</section>`;
}
function actions(incident) {
  const reviewing = incident.status === "review";
  return `<div class="decision-bar"><div>${statusBadge(incident.status)}<p>${incident.status === "approved" ? "No packet has been sent." : incident.status === "rejected" ? "Edit the packet to start a new review." : "Review the route and unknowns before approving."}</p></div><div class="action-buttons">${incident.status === "approved" ? `<button class="button secondary" data-action="share">${icon("link")}Copy local link</button>` : `<button class="button danger-quiet" data-action="reject" ${!reviewing ? "disabled" : ""}>Reject</button>`}<button class="button secondary" data-action="edit" ${incident.status === "running" ? "disabled" : ""}>${icon("edit")}Edit packet</button>${incident.status !== "approved" ? `<button class="button primary" data-action="approve" ${!reviewing ? "disabled" : ""}>${icon("check")}Approve packet</button>` : ""}</div></div>`;
}
function supporting(incident, ui) {
  const b = incident.building;
  return `<details class="supporting" ${ui.supporting ? "open" : ""}><summary>Building, sources & agent activity <span>${incident.stage}/5 stages complete</span></summary><div class="supporting-content"><section><h2>Building record</h2><h3>${e(b.name)}</h3><p>${e(b.address)}</p><dl class="building-facts"><div><dt>Use</dt><dd>${e(b.useType)}</dd></div><div><dt>Floors</dt><dd>${b.floors}</dd></div><div><dt>Area</dt><dd>${e(b.area)}</dd></div><div><dt>Tenant</dt><dd>${e(b.tenant)}</dd></div></dl><p>Demo scan · ${e(b.scanDate)}</p>${sourceButton("building", "View record")}</section><section><h2>Agent activity</h2><ol class="activity-list">${agentSteps.map((step, index) => `<li><div><strong>${step.name}</strong><span>${index < incident.stage ? step.duration : index === incident.stage && incident.status === "running" ? "Processing" : "Waiting"}</span></div><p>${index < incident.stage ? e(step.detail) : incident.awaitingCaller && index === incident.stage ? "Waiting for caller confirmation" : "Fixture stage pending"}</p>${index < incident.stage ? sourceButton(step.evidence, "View evidence") : ""}</li>`).join("")}</ol><button class="text-button" data-action="trace">Inspect incident trace ${icon("up")}</button></section></div></details>`;
}
export function consoleView(incident, ui, warning = "") {
  return `${header()}<main id="main" class="workspace">${destination(incident)}<div class="demo-toolbar"><span>Synthetic incident. Nothing is sent to callers or crews.</span><div><button class="text-button" data-action="about">About demo</button><button class="text-button" data-action="reset">${icon("reset")}Reset</button><button class="text-button" data-action="replay" ${incident.status === "running" ? "disabled" : ""}>${icon("play")}Replay incident</button></div></div>${warning ? `<div class="storage-warning" role="alert">${e(warning)}</div>` : ""}<div class="console-grid"><div class="operation"><div class="work-toolbar"><div class="tabs" role="tablist" aria-label="Location view">${[
    ["floor", "Interior route"],
    ["site", "Site approach"],
    ["match", "Photo match"],
  ]
    .map(
      ([key, label]) =>
        `<button class="tab ${ui.mapTab === key ? "active" : ""}" role="tab" id="tab-${key}" aria-controls="map-content" aria-selected="${ui.mapTab === key}" tabindex="${ui.mapTab === key ? "0" : "-1"}" data-action="map-tab" data-value="${key}">${label}</button>`,
    )
    .join(
      "",
    )}</div>${profiles(ui.profile)}</div><div class="mobile-links"><button class="text-button" data-action="caller-thread">Caller conversation</button><button class="text-button" data-action="review-packet">Packet review</button></div><div class="route-workspace">${mapPanel(incident, ui)}${incident.status === "running" ? '<section class="directions packet-waiting"><h2>Route pending</h2><p>The demo will prepare access instructions after caller confirmation.</p></section>' : directions(incident, ui.profile)}</div>${review(incident)}${actions(incident)}${supporting(incident, ui)}</div>${thread(incident)}</div></main>`;
}
export function packetBody(incident, { full = false, profile = "foot" } = {}) {
  const route = incident.routes[profile];
  const other = incident.routes[profile === "foot" ? "stretcher" : "foot"];
  return `<div class="packet-body"><div class="document-route">${floorMap(profile)}${directions(incident, profile)}</div><div class="review-columns">${unknowns(incident)}${notes(incident)}</div>${equipment()}${full ? `<section class="alternate-route"><h3>Alternate profile: ${e(other.label)}</h3><p>${e(other.steps.join(" "))}</p></section>` : ""}<p class="assumptions">Demo travel times and savings, not measured performance. ${route.savedSeconds} sec saved vs. front door in this fixture.</p></div>`;
}
export function fullPacketView(incident, profile, warning = "") {
  return `${header(true)}<main id="main" class="full-packet-wrapper"><nav class="packet-nav" aria-label="Packet actions"><a class="text-button" href="#console">${icon("arrow", "back-arrow")}Back to dispatch</a><div><button class="button secondary" data-action="download">${icon("download")}Markdown</button><button class="button secondary" data-action="print">${icon("print")}Print / PDF</button><button class="button primary" data-action="share" ${incident.status !== "approved" ? "disabled" : ""}>${icon("link")}Copy local link</button></div></nav>${warning ? `<div class="storage-warning" role="alert">${e(warning)}</div>` : ""}<article class="full-packet">${destination(incident, true)}<div class="document-state">${statusBadge(incident.status)}<span>Revision ${incident.revision} · Synthetic demo. Not for emergency use.</span>${profiles(profile, "Packet route profile")}</div>${incident.status === "running" ? '<div class="packet-waiting"><h2>Packet not ready</h2><p>Return to the console to finish the simulated incident.</p></div>' : packetBody(incident, { full: true, profile })}<div class="document-evidence"><figure><img src="/assets/lobby.png" alt="Synthetic fixture of east elevator lobby"/><figcaption>Reference lobby · synthetic image</figcaption></figure><section><h2>Sources</h2><p>All records are fixtures.</p><div class="document-sources">${incident.evidence.map((item) => sourceButton(item.id, `${item.source} · ${item.file}`)).join("")}</div><p>${e(incident.building.useType)} · ${incident.building.floors} floors · ${e(incident.building.tenant)}</p></section></div><footer class="document-footer"><span>${incident.status === "approved" ? "Approved locally" : "Not approved for sharing"}${incident.status === "rejected" ? ` · ${e(incident.decision?.reason)}` : ""}</span><span>No delivery service connected.</span></footer></article><p class="local-link-note">Local preview of the latest saved revision. Links work in this browser on this device while the server runs.</p></main>`;
}
