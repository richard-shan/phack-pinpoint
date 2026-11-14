import { createServer } from "node:http";
import { readFile, realpath } from "node:fs/promises";
import { dirname, extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const types = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".png": "image/png",
  ".ttf": "font/ttf",
  ".txt": "text/plain",
};
const port = Number(process.env.PORT || 4173);
const server = createServer(async (req, res) => {
  try {
    if (req.method !== "GET" && req.method !== "HEAD") {
      res.writeHead(405, { Allow: "GET, HEAD" }).end();
      return;
    }
    const pathname = decodeURIComponent(
      new URL(req.url, "http://localhost").pathname,
    );
    // Only browser assets are served; repository files and runtime code stay private.
    if (!(
      pathname === "/" ||
      pathname === "/index.html" ||
      /^\/(src|assets)\/[\w./-]+$/.test(pathname)
    )) {
      res.writeHead(404).end("Not found");
      return;
    }
    const file = await realpath(
      resolve(root, "." + (pathname === "/" ? "/index.html" : pathname)),
    );
    if (!file.startsWith(root + sep)) {
      res.writeHead(403).end("Forbidden");
      return;
    }
    const body = await readFile(file);
    res.writeHead(200, {
      "Content-Type": `${types[extname(file)] || "application/octet-stream"}; charset=utf-8`,
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy":
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    });
    res.end(req.method === "HEAD" ? undefined : body);
  } catch {
    res.writeHead(404).end("Not found");
  }
});
server.listen(port, "127.0.0.1", () =>
  console.log(`Pinpoint local demo: http://localhost:${port}`),
);
server.on("error", (error) => {
  console.error(error.message);
  process.exitCode = 1;
});
