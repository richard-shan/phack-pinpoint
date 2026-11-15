/** Thin client for phack-pinpoint's local console (`app/local-server.mjs`). */

const MAX_PHOTO = 2 * 1024 * 1024;

export type ConsoleError = Error & { status: number; code: string };

export type Incident = {
  id: string;
  revision: number;
  propertyId: string;
  status: string;
  messages: Array<{ id: string; role: "caller" | "dispatcher"; text: string; at: string }>;
  photo?: { name: string; dataUrl: string } | null;
};

export class ConsoleClient {
  constructor(
    readonly base = process.env.CONSOLE_URL ?? "http://localhost:4175",
    readonly timeoutMs = 10_000,
  ) {}

  private url(path: string) {
    return `${this.base.replace(/\/$/, "")}/api${path}`;
  }

  private async call<T>(method: string, path: string, body?: unknown, timeoutMs = this.timeoutMs): Promise<T> {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(this.url(path), {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: ctrl.signal,
      });
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (!res.ok) {
        const err = new Error(
          `console ${res.status} ${data?.error?.code ?? "ERROR"}: ${data?.error?.message ?? res.statusText}`,
        ) as ConsoleError;
        err.status = res.status;
        err.code = data?.error?.code ?? "ERROR";
        throw err;
      }
      return data as T;
    } finally {
      clearTimeout(timer);
    }
  }

  async alive(): Promise<boolean> {
    try {
      const h = await this.call<{ ok?: boolean }>("GET", "/health");
      return Boolean(h.ok);
    } catch {
      return false;
    }
  }

  get(id: string) {
    return this.call<Incident>("GET", `/incidents/${id}`);
  }

  list() {
    return this.call<{ incidents: Incident[] }>("GET", "/incidents").then((r) => r.incidents ?? []);
  }

  create(propertyId: string, callerText: string, photo?: { name: string; dataUrl: string }) {
    return this.call<Incident>("POST", "/incidents", {
      propertyId: String(propertyId),
      callerText: callerText || "",
      ...(photo ? { photo } : {}),
    });
  }

  createFromPhoto(callerText: string, photo: { name: string; dataUrl: string }) {
    return this.call<Incident>("POST", "/demo-photo", { callerText, photo }, 195_000);
  }

  private async write<T>(
    id: string,
    method: string,
    suffix: string,
    body: Record<string, unknown>,
    tries = 5,
  ): Promise<T> {
    for (let attempt = 0; attempt < tries; attempt++) {
      const rev = (await this.get(id)).revision;
      try {
        return await this.call<T>(method, `/incidents/${id}${suffix}`, {
          ...body,
          expectedRevision: rev,
        });
      } catch (e) {
        const err = e as ConsoleError;
        if (err.status !== 409 || err.code !== "REVISION_CONFLICT" || attempt === tries - 1) throw e;
        await new Promise((r) => setTimeout(r, 100 * (attempt + 1)));
      }
    }
    throw new Error("unreachable");
  }

  message(id: string, role: "caller" | "dispatcher", text: string) {
    return this.write<Incident>(id, "POST", "/messages", { role, text: text.slice(0, 4000) });
  }

  updatePhoto(id: string, photo: { name: string; dataUrl: string }) {
    return this.write<Incident>(id, "PATCH", "", { photo });
  }
}

export function photoFromBytes(bytes: Uint8Array, mimeType: string, name?: string) {
  let kind = mimeType.replace(/^image\//, "").toLowerCase();
  if (kind === "jpg") kind = "jpeg";
  if (!["jpeg", "png", "webp"].includes(kind)) {
    throw new Error(`console only accepts PNG/JPEG/WebP, got ${mimeType}`);
  }
  if (bytes.byteLength > MAX_PHOTO) {
    throw new Error(`photo is ${bytes.byteLength} bytes; console limit is ${MAX_PHOTO}`);
  }
  const b64 = Buffer.from(bytes).toString("base64");
  const file = name?.replace(/[^\w.-]+/g, "_").slice(0, 120) || `caller.${kind === "jpeg" ? "jpg" : kind}`;
  return { name: file, dataUrl: `data:image/${kind};base64,${b64}` };
}
