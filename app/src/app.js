import { createDemoAdapter, packetMarkdown } from "./adapter.js";
import { STORAGE_KEY, agentSteps } from "./fixtures.js";
import { consoleView, fullPacketView, escapeHtml as e, icon } from "./views.js";

let browserStorage;
try {
  browserStorage = window.localStorage;
} catch {
  browserStorage = {
    getItem() {
      throw new Error("Unavailable");
    },
  };
}
const adapter = createDemoAdapter({ storage: browserStorage });
const root = document.querySelector("#app");
const dialog = document.querySelector("#detail-dialog");
const toastElement = document.querySelector("#toast");
const ui = {
  mapTab: "floor",
  profile: "foot",
  zoom: false,
  message: "",
  supporting: false,
};
let toastTimer;
let dialogOpener;

function packetMode() {
  return location.hash.startsWith("#packet/");
}
function render() {
  const focused = document.activeElement;
  const focusId = focused?.id;
  const focusAction = focused?.dataset?.action;
  const focusValue = focused?.dataset?.value;
  const messageScroll = root.querySelector(".messages")?.scrollTop;
  const incident = adapter.getIncident();
  const packetId = location.hash.split("/")[1];
  if (packetMode() && packetId !== incident.id) {
    root.innerHTML =
      '<main id="main" class="not-found"><img src="/assets/mark.svg" width="50" alt="Pinpoint"/><h1>Packet not found locally</h1><p>This demo contains incident PP-1042 only. No backend is connected.</p><a class="button primary" href="#console">Return to console</a></main>';
    return;
  }
  root.innerHTML = packetMode()
    ? fullPacketView(incident, ui.profile, adapter.getPersistenceWarning())
    : consoleView(incident, ui, adapter.getPersistenceWarning());
  document.title = packetMode()
    ? `Packet ${incident.id} · Pinpoint`
    : `${incident.id} · Pinpoint dispatch`;
  const messageInput = root.querySelector("#message");
  if (messageInput) messageInput.value = ui.message;
  if (messageScroll != null && root.querySelector(".messages"))
    root.querySelector(".messages").scrollTop = messageScroll;
  if (dialog.open) return;
  if (focusId && document.getElementById(focusId))
    document.getElementById(focusId).focus({ preventScroll: true });
  else if (focusAction) {
    [...root.querySelectorAll("[data-action]")]
      .find(
        (element) =>
          element.dataset.action === focusAction &&
          element.dataset.value === focusValue,
      )
      ?.focus({ preventScroll: true });
  }
}

root.addEventListener(
  "toggle",
  (event) => {
    if (event.target.classList?.contains("supporting"))
      ui.supporting = event.target.open;
  },
  true,
);

function toast(message) {
  clearTimeout(toastTimer);
  toastElement.textContent = message;
  toastElement.hidden = false;
  toastTimer = setTimeout(() => {
    toastElement.hidden = true;
  }, 5000);
}
function openDialog(title, content, className = "") {
  dialogOpener = {
    action: document.activeElement?.dataset?.action,
    id: document.activeElement?.dataset?.id,
  };
  dialog.className = className;
  dialog.innerHTML = `<div class="dialog-heading"><h2 id="dialog-title">${e(title)}</h2><button class="icon-button" data-action="close-dialog" aria-label="Close dialog">${icon("close")}</button></div>${content}`;
  dialog.showModal();
}
function closeDialog() {
  dialog.close();
}
dialog.addEventListener("close", () => {
  [...root.querySelectorAll("[data-action]")]
    .find(
      (element) =>
        element.dataset.action === dialogOpener?.action &&
        element.dataset.id === dialogOpener?.id,
    )
    ?.focus({ preventScroll: true });
});
function showEvidence(id) {
  const evidence = adapter
    .getIncident()
    .evidence.find((item) => item.id === id);
  if (!evidence) return;
  openDialog(
    evidence.label,
    `<div class="evidence-meta"><span class="tiny-tag">${e(evidence.source)} FIXTURE</span><span>${e(evidence.file)} · ${e(evidence.confidence)}</span></div><p class="dialog-description">${e(evidence.summary)}</p>${evidence.image ? `<img class="evidence-image" src="${e(evidence.image)}" alt="Synthetic lobby evidence fixture"/>` : ""}<pre class="evidence-json">${e(JSON.stringify(evidence.fields, null, 2))}</pre><p class="dialog-footnote">Fixture source only. Replace with the backend’s source record, crop, or tile when connected.</p>`,
  );
}
function showEditor() {
  const incident = adapter.getIncident();
  openDialog(
    "Edit responder packet",
    `<p class="dialog-description">Update dispatcher notes and unconfirmed items. Saving creates a new revision and requires approval again.</p><form id="edit-form"><label for="packet-notes">Dispatcher notes</label><textarea id="packet-notes" name="notes" rows="5" maxlength="3000" required>${e(incident.packet.notes)}</textarea><label for="packet-unknowns">Not confirmed <span>one item per line</span></label><textarea id="packet-unknowns" name="unconfirmed" rows="4">${e(incident.packet.unconfirmed.join("\n"))}</textarea><p class="form-error" role="alert" hidden></p><div class="dialog-actions"><button type="button" class="button secondary" data-action="close-dialog">Cancel</button><button class="button primary" type="submit">Save revision ${icon("check")}</button></div></form>`,
  );
}
function showReject() {
  openDialog(
    "Request a revision",
    `<p class="dialog-description">The packet will stay unapproved. Record what needs to change for the next review.</p><form id="reject-form"><label for="reject-reason">Reason for rejection</label><textarea id="reject-reason" name="reason" rows="4" maxlength="1000" placeholder="For example: verify that the east entrance is accessible." required></textarea><p class="form-error" role="alert" hidden></p><div class="dialog-actions"><button class="button secondary" type="button" data-action="close-dialog">Cancel</button><button class="button reject-button" type="submit">Reject packet</button></div></form>`,
  );
}
async function share() {
  const incident = adapter.getIncident();
  if (incident.status !== "approved")
    throw new Error("Approve this revision before copying a packet link.");
  if (adapter.getPersistenceWarning())
    throw new Error(
      "Local persistence is unavailable. Export Markdown to preserve this packet.",
    );
  const url = new URL(location.href);
  url.hash = `packet/${incident.id}`;
  try {
    await navigator.clipboard.writeText(url.href);
    toast("Local packet link copied. Works on this device only.");
  } catch {
    openDialog(
      "Local packet link",
      `<p class="dialog-description">Copy this link. It opens the latest saved revision on this browser and device only.</p><label for="local-url">Packet URL</label><input id="local-url" readonly value="${e(url.href)}"/>`,
    );
    dialog.querySelector("input").select();
  }
}
function downloadPacket() {
  const incident = adapter.getIncident();
  const url = URL.createObjectURL(
    new Blob([packetMarkdown(incident)], {
      type: "text/markdown;charset=utf-8",
    }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `pinpoint-${incident.id}-r${incident.revision}-${incident.status}.md`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast("Markdown packet downloaded.");
}
document.addEventListener("click", async (event) => {
  if (event.target.closest("a.skip-link")) {
    event.preventDefault();
    const main = document.querySelector("#main");
    main?.setAttribute("tabindex", "-1");
    main?.focus();
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button || button.disabled) return;
  const { action, value, id } = button.dataset;
  try {
    switch (action) {
      case "caller-thread":
      case "review-packet": {
        const packet = document.getElementById(action === "caller-thread" ? "caller-heading" : "responder-packet");
        packet?.scrollIntoView({
          behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "instant"
            : "smooth",
          block: "start",
        });
        packet?.focus({ preventScroll: true });
        break;
      }
      case "map-tab":
        ui.mapTab = value;
        render();
        break;
      case "profile":
        ui.profile = value;
        render();
        break;
      case "zoom":
        ui.zoom = !ui.zoom;
        render();
        break;
      case "evidence":
        showEvidence(id);
        break;
      case "close-dialog":
        closeDialog();
        break;
      case "edit":
        showEditor();
        break;
      case "reject":
        showReject();
        break;
      case "approve":
        adapter.decide("approved");
        toast("Packet approved locally. No messages were sent.");
        break;
      case "share":
        await share();
        break;
      case "print":
        window.print();
        break;
      case "download":
        downloadPacket();
        break;
      case "replay":
        adapter.replay();
        toast(
          "Demo replay started. Simulate the caller’s reply when prompted.",
        );
        break;
      case "reply":
        adapter.simulateCallerReply();
        scrollThread();
        break;
      case "reset":
        openDialog(
          "Reset local demo?",
          `<p class="dialog-description">This replaces local messages, photo uploads, edits, and approval decisions with the original fixture.</p><div class="dialog-actions"><button class="button secondary" data-action="close-dialog">Keep changes</button><button class="button primary" data-action="confirm-reset">Reset demo</button></div>`,
        );
        break;
      case "confirm-reset":
        ui.message = "";
        adapter.reset();
        closeDialog();
        toast("Original fixture restored.");
        break;
      case "upload":
        document.querySelector("#photo-input").click();
        break;
      case "photo": {
        const incident = adapter.getIncident();
        openDialog(
          "Caller photo",
          `<img class="evidence-image" src="${e(incident.callerPhoto)}" alt="Caller photo preview"/><p class="dialog-footnote">${incident.photoIsUpload ? "Uploaded locally; no matching or external upload takes place." : "AI-generated demo image, not a real caller or building."}</p>`,
        );
        break;
      }
      case "trace": {
        const incident = adapter.getIncident();
        openDialog(
          "Incident trace",
          `<p class="dialog-description">Simulated tool activity for ${e(incident.id)}. These timings are fixture values.</p><pre class="evidence-json">${
            e(
              agentSteps
                .slice(0, incident.stage)
                .map((step, i) =>
                  JSON.stringify({
                    sequence: i + 1,
                    agent: step.agent,
                    event: step.name,
                    duration: step.duration,
                    source: incident.evidence.find(
                      (item) => item.id === step.evidence,
                    )?.file,
                  }),
                )
                .join("\n"),
            ) || "No completed steps yet."
          }</pre>`,
        );
        break;
      }
      case "about":
        openDialog(
          "A clearer way in",
          `<p class="dialog-description">Pinpoint joins building records, indoor imagery, and aerial context to help a dispatcher review a route to a caller.</p><div class="about-list"><p><strong>This workspace is a local prototype.</strong> All building details, locations, routes, imagery, agent events, and confidence values are synthetic fixtures.</p><p>Approvals and conversations stay in this browser. Photon, image matching, routing, and agent services are not connected.</p><p>Photo uploads are only previewed. A local packet link is a live view of browser storage, not a publicly shareable or immutable delivery link.</p></div>`,
        );
        break;
    }
  } catch (error) {
    toast(error.message || "That action could not be completed.");
  }
});
function scrollThread() {
  const thread = root.querySelector(".messages");
  if (thread) thread.scrollTop = thread.scrollHeight;
}
document.addEventListener("input", (event) => {
  if (event.target.id === "message") ui.message = event.target.value;
});
document.addEventListener("submit", (event) => {
  const form = event.target;
  event.preventDefault();
  try {
    const data = new FormData(form);
    if (form.id === "message-form") {
      const text = data.get("message");
      ui.message = "";
      adapter.sendMessage(text);
      scrollThread();
      document.querySelector("#message")?.focus();
    } else if (form.id === "edit-form") {
      adapter.savePacket({
        notes: data.get("notes"),
        unconfirmed: data
          .get("unconfirmed")
          .split("\n")
          .filter((line) => line.trim()),
      });
      closeDialog();
      toast("New revision saved. Dispatcher approval is required again.");
    } else if (form.id === "reject-form") {
      adapter.decide("rejected", data.get("reason"));
      closeDialog();
      toast("Packet rejected. Edit it to begin a new review.");
    }
  } catch (error) {
    const output = form.querySelector(".form-error");
    if (output) {
      output.textContent = error.message;
      output.hidden = false;
    } else toast(error.message);
  }
});
document.addEventListener("change", async (event) => {
  if (event.target.id !== "photo-input") return;
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type))
      throw new Error("Choose a JPG, PNG, or WebP image.");
    if (file.size > 2 * 1024 * 1024)
      throw new Error("Choose an image smaller than 2 MB.");
    const data = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Could not read this image."));
      reader.readAsDataURL(file);
    });
    const image = new Image();
    image.src = data;
    await image.decode();
    adapter.replay(data);
    ui.mapTab = "match";
    render();
    toast(
      "Photo stays local. Replaying fixture results; no matching is performed.",
    );
  } catch (error) {
    toast(error.message || "This image could not be opened.");
  }
  event.target.value = "";
});
document.addEventListener("keydown", (event) => {
  const tab = event.target.closest('[role="tab"]');
  if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
    return;
  event.preventDefault();
  const values = ["floor", "site", "match"];
  const index = values.indexOf(tab.dataset.value);
  ui.mapTab =
    event.key === "Home"
      ? values[0]
      : event.key === "End"
        ? values[2]
        : values[(index + (event.key === "ArrowRight" ? 1 : -1) + 3) % 3];
  render();
  document.getElementById(`tab-${ui.mapTab}`).focus();
});
window.addEventListener("hashchange", () => {
  if (location.hash === "#main") return;
  if (dialog.open) dialog.close();
  render();
  window.scrollTo(0, 0);
});
window.addEventListener("storage", (event) => {
  if (event.key === STORAGE_KEY && event.newValue)
    adapter.acceptExternal(event.newValue);
});
adapter.subscribe(render);
render();
