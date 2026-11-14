import { createIncident, STORAGE_KEY } from "./fixtures.js";

const clone = (value) => structuredClone(value);
const time = () =>
  new Date().toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

export function isIncident(value) {
  const base = createIncident();
  const sameShape = (sample, input) => {
    if (sample === null) return input === null || typeof input === "object";
    if (Array.isArray(sample))
      return (
        Array.isArray(input) &&
        input.every((item) => !sample.length || sameShape(sample[0], item))
      );
    if (typeof sample === "object")
      return (
        input &&
        typeof input === "object" &&
        Object.keys(sample)
          .filter((key) => key !== "image")
          .every((key) => sameShape(sample[key], input[key]))
      );
    return typeof sample === typeof input;
  };
  return (
    sameShape({ ...base, evidence: [] }, value) &&
    value.schemaVersion === 1 &&
    value.id === base.id &&
    ["review", "running", "approved", "rejected"].includes(value.status) &&
    Number.isInteger(value.stage) &&
    value.stage >= 0 &&
    value.stage <= 5 &&
    ["foot", "stretcher"].every((key) => value.routes[key].steps.length > 0) &&
    value.evidence.length === base.evidence.length &&
    value.evidence.every((item, index) =>
      sameShape(base.evidence[index], item),
    ) &&
    (["/assets/lobby.svg", "/assets/lobby.png"].includes(value.callerPhoto) ||
      /^data:image\/(jpeg|png|webp);base64,/.test(value.callerPhoto)) &&
    (value.status !== "approved" ||
      (value.decision?.kind === "approved" &&
        value.decision.revision === value.revision))
  );
}

/** Replace this adapter with the team's backend. Views consume snapshots, never files directly. */
export function createDemoAdapter({
  storage = globalThis.localStorage,
  delay = 1000,
  schedule = setTimeout,
  cancel = clearTimeout,
} = {}) {
  let incident = createIncident();
  let persistenceError = "";
  try {
    const saved = storage?.getItem(STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (isIncident(parsed)) {
        // Preserve existing edits and approvals while upgrading the demo image.
        if (parsed.callerPhoto === "/assets/lobby.svg")
          parsed.callerPhoto = "/assets/lobby.png";
        for (const item of parsed.evidence) {
          if (item.image === "/assets/lobby.svg")
            item.image = "/assets/lobby.png";
          if (item.fields.evidence_path === "/assets/lobby.svg")
            item.fields.evidence_path = "/assets/lobby.png";
        }
        incident = parsed;
      } else
        persistenceError =
          "Saved demo was incompatible. A fresh fixture was loaded.";
    }
  } catch {
    persistenceError =
      "Local storage is unavailable. Changes will last for this tab only.";
  }
  const subscribers = new Set();
  let timer;
  let generation = 0;

  function persist() {
    try {
      storage?.setItem(STORAGE_KEY, JSON.stringify(incident));
    } catch {
      persistenceError =
        "Local storage is full or unavailable. Changes will last for this tab only.";
    }
  }
  function publish() {
    persist();
    subscribers.forEach((listener) => listener(clone(incident)));
  }
  function stop() {
    generation++;
    if (timer) cancel(timer);
    timer = undefined;
  }
  function message(role, text) {
    incident.messages.push({
      id: `m-${Date.now()}-${incident.messages.length}`,
      role,
      text,
      time: time(),
    });
  }
  function advance() {
    const run = generation;
    timer = schedule(() => {
      if (run !== generation || incident.status !== "running") return;
      incident.stage++;
      if (incident.stage === 1) {
        incident.awaitingCaller = true;
        message(
          "agent",
          "Demo question: is there a blue 204 sign beside the elevator?",
        );
      } else if (incident.stage >= 5) {
        incident.status = "review";
        incident.elapsedSeconds = 38;
      }
      publish();
      if (incident.status === "running" && !incident.awaitingCaller) advance();
    }, delay);
  }
  const api = {
    getIncident: () => clone(incident),
    getPersistenceWarning: () => persistenceError,
    subscribe(listener) {
      subscribers.add(listener);
      return () => subscribers.delete(listener);
    },
    sendMessage(text) {
      const trimmed = text.trim();
      if (!trimmed || trimmed.length > 1000)
        throw new Error("Write a message between 1 and 1,000 characters.");
      message("dispatcher", trimmed);
      publish();
    },
    simulateCallerReply() {
      if (!incident.awaitingCaller) return;
      message("caller", "Yes, just to my right. (Simulated reply)");
      incident.awaitingCaller = false;
      publish();
      advance();
    },
    savePacket({ notes, unconfirmed }) {
      if (incident.status === "running")
        throw new Error("Wait for the demo run to finish before editing.");
      if (!notes.trim() || notes.length > 3000)
        throw new Error("Add dispatcher notes of up to 3,000 characters.");
      if (
        !Array.isArray(unconfirmed) ||
        unconfirmed.length > 20 ||
        unconfirmed.some(
          (item) => typeof item !== "string" || item.length > 500,
        )
      )
        throw new Error(
          "Use up to 20 unconfirmed items of 500 characters each.",
        );
      incident.packet.notes = notes.trim();
      incident.packet.unconfirmed = unconfirmed
        .map((item) => item.trim())
        .filter(Boolean);
      incident.revision++;
      incident.status = "review";
      incident.decision = null;
      publish();
    },
    decide(kind, reason = "") {
      if (incident.status !== "review")
        throw new Error(
          "Only a packet awaiting review can be approved or rejected.",
        );
      if (!["approved", "rejected"].includes(kind))
        throw new Error("Unknown decision.");
      if (kind === "rejected" && !reason.trim())
        throw new Error("Add a reason so the packet can be revised.");
      incident.status = kind;
      incident.decision = {
        kind,
        reason: reason.trim(),
        at: new Date().toISOString(),
        revision: incident.revision,
      };
      publish();
    },
    replay(photo) {
      if (photo && !/^data:image\/(jpeg|png|webp);base64,/.test(photo))
        throw new Error("Use a PNG, JPEG, or WebP image.");
      stop();
      incident = createIncident();
      incident.status = "running";
      incident.stage = 0;
      incident.elapsedSeconds = 0;
      incident.messages = incident.messages.slice(0, 2);
      if (photo) {
        incident.callerPhoto = photo;
        incident.photoIsUpload = true;
        incident.messages[1].text =
          "Local photo attached. Results remain the synthetic demo fixture.";
      }
      publish();
      advance();
    },
    reset() {
      stop();
      incident = createIncident();
      publish();
    },
    acceptExternal(serialized) {
      try {
        const next = JSON.parse(serialized);
        if (!isIncident(next)) return;
        stop();
        incident = next;
        subscribers.forEach((listener) => listener(clone(incident)));
      } catch {
        /* Ignore unrelated or malformed browser storage events. */
      }
    },
    dispose() {
      stop();
      subscribers.clear();
    },
  };
  if (incident.status === "running" && !incident.awaitingCaller) advance();
  // Save the initial fixture so the standalone packet can resolve the same local incident.
  persist();
  return api;
}

export function packetMarkdown(incident) {
  const { building, location, packet, routes } = incident;
  return [
    `# Pinpoint responder packet · ${incident.id}`,
    "",
    "**SYNTHETIC DEMO — NOT FOR EMERGENCY USE**",
    `Status: ${incident.status.toUpperCase()} · Revision ${incident.revision}`,
    `Building: ${building.name} — ${building.address}`,
    `Location: Floor ${location.floor}, ${location.room}, Suite ${location.suite} (${Math.round(location.confidence * 100)}% fixture confidence)`,
    ...Object.values(routes).flatMap((route) => [
      "",
      `## ${route.label}`,
      `Stage: ${route.staging}. Enter: ${route.entrance} (${route.door}).`,
      `${route.meters} m · ${route.seconds} sec estimated · via ${route.vertical}`,
      ...route.steps.map((step, index) => `${index + 1}. ${step}`),
    ]),
    "",
    "## Dispatcher notes",
    packet.notes,
    "",
    "## Building & equipment",
    `${building.useType} · ${building.floors} floors · ${building.tenant}`,
    "AED: east elevator lobby. Extinguisher: Stair B. Availability unconfirmed.",
    "",
    "## Not confirmed",
    ...(packet.unconfirmed.length
      ? packet.unconfirmed.map((item) => `- ${item}`)
      : ["No items listed by dispatcher; this is not verification."]),
    "",
    "## Fixture sources",
    ...incident.evidence.map(
      (item) => `- ${item.source}: ${item.file} — ${item.label}`,
    ),
    ...(incident.decision
      ? [
          "",
          `Decision: ${incident.decision.kind} at ${incident.decision.at}`,
          incident.decision.reason,
        ]
      : []),
  ].join("\n");
}
