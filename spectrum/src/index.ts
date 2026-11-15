import { Spectrum } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";
import { PhotonConsoleBridge } from "./bridge.js";

if (!process.env.PROJECT_ID || !process.env.PROJECT_SECRET) {
  throw new Error("Set PROJECT_ID and PROJECT_SECRET in spectrum/.env before starting the bridge.");
}
const bridge = new PhotonConsoleBridge();
await bridge.start();
const app = await Spectrum({ projectId: process.env.PROJECT_ID, projectSecret: process.env.PROJECT_SECRET, providers: [imessage.config()] });
console.log("[spectrum] listening for iMessage");

// Serialize each chat so a text followed by a photo arrives in order, without
// blocking other conversations during the potentially slow demo photo match.
const queues = new Map<string, Promise<void>>();
for await (const [space, message] of app.messages) {
  const previous = queues.get(space.id) || Promise.resolve();
  const next = previous.catch(() => {}).then(() => bridge.onInbound(space, message)).catch(async (err) => {
    console.error("[bridge] inbound failed:", err instanceof Error ? err.message : err);
    try { await space.send("The demo could not process that message. Please alert the demo operator."); } catch { /* provider may be unavailable */ }
  });
  queues.set(space.id, next);
  void next.finally(() => { if (queues.get(space.id) === next) queues.delete(space.id); });
}
