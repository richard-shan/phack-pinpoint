import test from "node:test";
import assert from "node:assert/strict";
import {
  createDemoAdapter,
  isIncident,
  packetMarkdown,
} from "../src/adapter.js";
import { createIncident, STORAGE_KEY } from "../src/fixtures.js";
import { escapeHtml, consoleView, fullPacketView } from "../src/views.js";

function harness(initial) {
  const data = new Map(initial ? [[STORAGE_KEY, JSON.stringify(initial)]] : []);
  const pending = new Map();
  let sequence = 0;
  const adapter = createDemoAdapter({
    storage: {
      getItem: (key) => data.get(key),
      setItem: (key, value) => data.set(key, value),
    },
    schedule: (callback) => {
      pending.set(++sequence, callback);
      return sequence;
    },
    cancel: (id) => pending.delete(id),
  });
  return {
    adapter,
    data,
    tick() {
      const next = pending.entries().next().value;
      if (next) {
        pending.delete(next[0]);
        next[1]();
      }
    },
    get pendingCount() {
      return pending.size;
    },
  };
}

test("valid fixture and approved revision survive browser persistence", () => {
  assert.equal(isIncident(createIncident()), true);
  const h = harness();
  h.adapter.decide("approved");
  const saved = JSON.parse(h.data.get(STORAGE_KEY));
  assert.equal(isIncident(saved), true);
  const restored = harness(saved).adapter.getIncident();
  assert.equal(restored.status, "approved");
  assert.equal(restored.decision.revision, restored.revision);
});

test("editing an approved packet invalidates approval and increments revision", () => {
  const { adapter } = harness();
  adapter.decide("approved");
  assert.throws(() => adapter.decide("approved"), /awaiting review/);
  adapter.savePacket({
    notes: "Use the south entrance.",
    unconfirmed: ["Verify elevator operation."],
  });
  assert.equal(adapter.getIncident().status, "review");
  assert.equal(adapter.getIncident().revision, 2);
  assert.equal(adapter.getIncident().decision, null);
  adapter.decide("approved");
  assert.equal(adapter.getIncident().decision.revision, 2);
});

test("rejection requires a reason and editing reopens review", () => {
  const { adapter } = harness();
  assert.throws(() => adapter.decide("rejected", "  "), /reason/);
  adapter.decide("rejected", "Confirm east access.");
  assert.equal(adapter.getIncident().decision.reason, "Confirm east access.");
  assert.throws(() => adapter.decide("approved"), /awaiting review/);
  adapter.savePacket({
    notes: "Access is still unconfirmed.",
    unconfirmed: ["East door access."],
  });
  assert.equal(adapter.getIncident().status, "review");
});

test("replay pauses for caller, blocks approval and resumes through verifier", () => {
  const h = harness();
  h.adapter.replay();
  assert.equal(h.adapter.getIncident().stage, 0);
  assert.throws(() => h.adapter.decide("approved"), /awaiting review/);
  h.tick();
  assert.equal(h.adapter.getIncident().awaitingCaller, true);
  assert.equal(h.pendingCount, 0);
  h.adapter.simulateCallerReply();
  for (let i = 0; i < 4; i++) h.tick();
  assert.equal(h.adapter.getIncident().status, "review");
  assert.equal(h.adapter.getIncident().stage, 5);
  assert.equal(h.pendingCount, 0);
});

test("reset cancels queued simulation updates", () => {
  const h = harness();
  h.adapter.replay();
  h.adapter.reset();
  h.tick();
  assert.equal(h.adapter.getIncident().status, "review");
  assert.equal(h.adapter.getIncident().stage, 5);
  assert.equal(h.pendingCount, 0);
});

test("malformed or stale approval storage falls back to a fresh incident", () => {
  const malformed = createIncident();
  malformed.routes = null;
  assert.equal(isIncident(malformed), false);
  const stale = createIncident();
  stale.status = "approved";
  stale.decision = { kind: "approved", revision: 99 };
  assert.equal(isIncident(stale), false);
  const h = harness(stale);
  assert.equal(h.adapter.getIncident().status, "review");
  assert.match(h.adapter.getPersistenceWarning(), /incompatible/);
});

test("storage failure leaves the current session usable", () => {
  const adapter = createDemoAdapter({
    storage: {
      getItem() {
        throw Error();
      },
      setItem() {
        throw Error();
      },
    },
  });
  adapter.decide("approved");
  assert.equal(adapter.getIncident().status, "approved");
  assert.match(adapter.getPersistenceWarning(), /unavailable/);
});

test("snapshots do not expose mutable internal state", () => {
  const { adapter } = harness();
  const snapshot = adapter.getIncident();
  snapshot.packet.notes = "Changed externally";
  assert.notEqual(adapter.getIncident().packet.notes, snapshot.packet.notes);
});

test("free text stays escaped in console and packet, exports include both profiles and unknowns", () => {
  const incident = createIncident();
  incident.packet.notes = "<img src=x onerror=alert(1)>";
  const ui = { profile: "foot", mapTab: "floor", zoom: false };
  for (const html of [
    consoleView(incident, ui),
    fullPacketView(incident, "foot"),
  ]) {
    assert.ok(html.includes(escapeHtml(incident.packet.notes)));
    assert.ok(!html.includes(incident.packet.notes));
  }
  const markdown = packetMarkdown(incident);
  assert.match(markdown, /SYNTHETIC DEMO/);
  assert.match(markdown, /Crew on foot/);
  assert.match(markdown, /Stretcher/);
  assert.match(markdown, /Elevator is operational/);
});
