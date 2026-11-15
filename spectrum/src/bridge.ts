import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import type { Message, Space } from "spectrum-ts";
import { ConsoleClient, photoFromBytes, type Incident } from "./console.js";

type Link = { incidentId: string; sentDispatcherIds: string[] };
type State = { links: Record<string, Link>; pendingText?: Record<string, string> };
const STATE_PATH = resolve(process.cwd(), ".bridge-state.json");

export class PhotonConsoleBridge {
  private state: State;
  private spaces = new Map<string, Space>();
  private pollTimer?: ReturnType<typeof setInterval>;
  private polling = false;

  constructor(
    readonly console = new ConsoleClient(),
    readonly propertyId = process.env.CONSOLE_PROPERTY_ID || undefined,
    readonly pinnedIncidentId = process.env.CONSOLE_INCIDENT_ID || undefined,
    readonly statePath = STATE_PATH,
  ) {
    this.state = existsSync(statePath) ? JSON.parse(readFileSync(statePath, "utf8")) as State : { links: {} };
    this.state.pendingText ??= {};
  }

  private save() { writeFileSync(this.statePath, JSON.stringify(this.state, null, 2), { mode: 0o600 }); }

  async start() {
    if (!(await this.console.alive())) throw new Error(`Console not reachable at ${this.console.base}. Start python3 -m live.serve from the repository root.`);
    if (this.pinnedIncidentId) await this.console.get(this.pinnedIncidentId);
    console.log(`[bridge] console connected; ${this.propertyId ? `fixed demo building ${this.propertyId}` : "photo-based demo building inference"}`);
    this.pollTimer = setInterval(() => {
      if (this.polling) return;
      this.polling = true;
      void this.flushDispatcherOutbound().catch((err) => console.error("[bridge] outbound poll failed:", err instanceof Error ? err.message : err)).finally(() => { this.polling = false; });
    }, 1500);
  }
  stop() { if (this.pollTimer) clearInterval(this.pollTimer); }

  private async pin(spaceId: string): Promise<Link | undefined> {
    const existing = this.state.links[spaceId];
    if (existing || !this.pinnedIncidentId) return existing;
    const inc = await this.console.get(this.pinnedIncidentId);
    const link = { incidentId: inc.id, sentDispatcherIds: inc.messages.filter(m => m.role === "dispatcher").map(m => m.id) };
    this.state.links[spaceId] = link; this.save(); return link;
  }

  async onInbound(space: Space, message: Message) {
    if (message.direction !== "inbound") return;
    this.spaces.set(space.id, space);
    const content = message.content.type === "effect" ? message.content.content : message.content;
    if (content.type === "text" && content.text.trim()) {
      const text = content.text.trim();
      const link = await this.pin(space.id);
      if (link) await this.console.message(link.incidentId, "caller", text);
      else {
        const prior = this.state.pendingText![space.id] || "";
        this.state.pendingText![space.id] = [prior, text].filter(Boolean).join("\n").slice(0, 4000);
        this.save();
        if (!prior) await space.send("Message received. Please send a photo so the demo can find the building. This is not an emergency service.");
      }
      return;
    }
    if (content.type !== "attachment" || !content.mimeType.startsWith("image/")) return;
    try {
      if (typeof content.size === "number" && content.size > 2 * 1024 * 1024) throw new Error("The image exceeds 2 MB. Please send a smaller JPEG, PNG, or WebP.");
      const photo = photoFromBytes(await content.read(), content.mimeType, content.name);
      const link = await this.pin(space.id);
      if (link) {
        await this.console.updatePhoto(link.incidentId, photo);
        await this.console.message(link.incidentId, "caller", "[photo received via iMessage]");
      } else {
        const text = this.state.pendingText![space.id] || "Caller sent a photo via iMessage.";
        const inc = this.propertyId ? await this.console.create(this.propertyId, text, photo) : await this.console.createFromPhoto(text, photo);
        this.state.links[space.id] = { incidentId: inc.id, sentDispatcherIds: inc.messages.filter(m => m.role === "dispatcher").map(m => m.id) };
        delete this.state.pendingText![space.id]; this.save();
        console.log(`[bridge] photo opened incident ${inc.id}`);
      }
      await space.send("Photo received by the demo console for review. This is not an emergency service.");
    } catch (err) {
      const code = (err as { code?: string }).code;
      if (code === "DEMO_MATCH_UNCERTAIN") await space.send("The demo could not identify the building from that photo. Please try another supported demo photo.");
      else if (code === "DEMO_MATCH_UNAVAILABLE") await space.send("The demo photo matcher is unavailable. Please alert the demo operator.");
      else if (["INVALID_PHOTO", "PHOTO_TOO_LARGE"].includes(code || "") || (err instanceof Error && /console only accepts|photo is |exceeds 2 MB/.test(err.message))) await space.send("Please send a JPEG, PNG, or WebP image under 2 MB.");
      else throw err;
    }
  }

  async flushDispatcherOutbound() {
    for (const [spaceId, link] of Object.entries(this.state.links)) {
      const space = this.spaces.get(spaceId); if (!space) continue;
      let inc: Incident;
      try { inc = await this.console.get(link.incidentId); } catch { continue; }
      for (const m of inc.messages.filter(m => m.role === "dispatcher" && !link.sentDispatcherIds.includes(m.id))) {
        await space.send(m.text.replace(/^Pinpoint asks:\s*/i, "").trim() || m.text);
        link.sentDispatcherIds.push(m.id); this.save();
      }
    }
  }
}
