import { vexcelService, aerialBounds } from './lib/vexcel-service.mjs';
import { createServer } from 'node:http';
import { readFile, realpath } from 'node:fs/promises';
import { dirname, extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createLocalStore, ServiceError } from './lib/local-store.mjs';
import { spatialService, princetonProperty, viewerCSP } from './lib/spatial-service.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const mime = { '.html':'text/html', '.js':'text/javascript', '.css':'text/css', '.json':'application/json', '.svg':'image/svg+xml', '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.webp':'image/webp', '.ttf':'font/ttf', '.woff2':'font/woff2', '.txt':'text/plain', '.ico':'image/x-icon', '.mp4':'video/mp4', '.vtt':'text/vtt', '.webm':'video/webm', '.pdf':'application/pdf', '.wasm':'application/wasm', '.gql':'text/plain', '.woff':'font/woff', '.bin':'application/octet-stream', '.glb':'model/gltf-binary', '.cjs':'text/javascript' };
const MAX_BODY = 3 * 1024 * 1024;
const securityHeaders = {
  'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'no-referrer',
  'Content-Security-Policy': "default-src 'self'; img-src 'self' data: blob: https://my.matterport.com; frame-src 'self' https://my.matterport.com; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
};
function error(status, code, message) { throw new ServiceError(status, code, message); }
async function jsonBody(req) {
  if (!/^application\/json(?:\s*;|$)/i.test(req.headers['content-type'] || '')) error(415, 'JSON_REQUIRED', 'Use application/json.');
  if (Number(req.headers['content-length']) > MAX_BODY) error(413, 'BODY_TOO_LARGE', 'The request is too large.');
  let size = 0; const chunks = [];
  for await (const chunk of req) { size += chunk.length; if (size > MAX_BODY) error(413, 'BODY_TOO_LARGE', 'The request is too large.'); chunks.push(chunk); }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { error(400, 'INVALID_JSON', 'The request body is not valid JSON.'); }
}
export function createLocalServer({ root = here, stateDirectory = resolve(root, '.local-state'), aerialFetch = fetch } = {}) {
  root = resolve(root);
  const canonicalRoot = realpath(root);
  const getCatalog = async () => {
    try {
      const catalog = JSON.parse(await readFile(resolve(root, 'data/catalog.json'), 'utf8'));
      if (!Array.isArray(catalog.properties)) error(503, 'CATALOG_UNAVAILABLE', 'The local catalog is not valid.');
      if (!catalog.properties.some(p => p.id === princetonProperty.id)) catalog.properties.unshift(princetonProperty);
      return catalog;
    } catch (e) { if (e instanceof ServiceError) throw e; error(503, 'CATALOG_UNAVAILABLE', 'The real-data catalog is not ready. Build it before creating incidents.'); }
  };
  const store = createLocalStore({ directory: stateDirectory, getCatalog });
  const spatial = spatialService(root,store);
  const aerial = vexcelService(root,aerialFetch);
  const imageCache=new Map();
  async function aerialImage(id,bounds,width=1400,height=1400){
    const cached=imageCache.get(id);if(cached&&cached.expires>Date.now())return cached.image;
    if(imageCache.size>=16)imageCache.delete(imageCache.keys().next().value);
    const image=aerial.extract(bounds,width,height);imageCache.set(id,{image,expires:Date.now()+15*60*1000});
    try{return await image;}catch(error){imageCache.delete(id);throw error;}
  }
  const server = createServer(async (req, res) => {
    const send = (status, value) => { res.writeHead(status, { ...securityHeaders, 'Content-Type':'application/json; charset=utf-8' }); res.end(req.method === 'HEAD' ? undefined : JSON.stringify(value)); };
    try {
      const port = server.address()?.port;
      const hosts = [`127.0.0.1:${port}`, `localhost:${port}`];
      if (!hosts.includes(req.headers.host)) error(403, 'HOST_REJECTED', 'Use this server through its local loopback address.');
      if (req.headers.origin && req.headers.origin !== `http://${req.headers.host}`) error(403, 'ORIGIN_REJECTED', 'Cross-origin requests are not allowed.');
      if (req.headers['sec-fetch-site'] === 'cross-site' && !(req.method === 'GET' && req.headers['sec-fetch-mode'] === 'navigate' && req.headers['sec-fetch-dest'] === 'document')) error(403, 'ORIGIN_REJECTED', 'Cross-site requests are not allowed.');
      let pathname;
      try { pathname = decodeURIComponent(new URL(req.url, `http://${req.headers.host}`).pathname); } catch { error(400, 'INVALID_URL', 'Invalid URL.'); }
      const method = req.method;
      if (pathname.startsWith('/api/')) {
        if (method === 'GET' && pathname === '/api/aerial/config') return send(200,aerial.config());
        if (method === 'GET' && pathname === '/api/aerial/princeton.jpg') {
          const b=aerialBounds,height=Math.round(1400*(b.north-b.south)/((b.east-b.west)*Math.cos((b.north+b.south)/2*Math.PI/180)));
          const image=await aerialImage('princeton',b,1400,height);
          res.writeHead(200,{...securityHeaders,'Content-Type':'image/jpeg','Content-Length':image.length});return res.end(image);
        }
        const aerialProperty = /^\/api\/aerial\/([a-zA-Z0-9-]+)\.jpg$/.exec(pathname);
        if (method === 'GET' && aerialProperty) {
          const property=(await getCatalog()).properties.find(p=>p.id===aerialProperty[1]);
          if(!property) error(404,'NOT_FOUND','Property not found.');
          if(!['Los Angeles','Houston'].includes(property.city)) error(422,'VEXCEL_OUTSIDE_COVERAGE','This catalog property is outside the supported demo cities.');
          const lat=property.latitude,lon=property.longitude;
          if(!Number.isFinite(lat)||!Number.isFinite(lon)) error(422,'LOCATION_UNAVAILABLE','This property has no verified coordinates.');
          const dy=70/111320,dx=dy/Math.cos(lat*Math.PI/180);
          const image=await aerialImage(property.id,{west:lon-dx,south:lat-dy,east:lon+dx,north:lat+dy});
          res.writeHead(200,{...securityHeaders,'Content-Type':'image/jpeg','Content-Length':image.length});
          return res.end(image);
        }
        if (method === 'GET' && pathname === '/api/spatial/config') return send(200,await spatial.config(new URL(req.url,`http://${req.headers.host}`).searchParams.get('propertyId')||undefined));
        if (method === 'GET' && pathname === '/api/spatial/model') return send(200,await spatial.model(new URL(req.url,`http://${req.headers.host}`).searchParams.get('propertyId')||undefined));
        const spatialUpdate = /^\/api\/incidents\/(inc-[a-f0-9-]{36})\/route$/.exec(pathname);
        if (spatialUpdate && method === 'POST') {
          const body=await jsonBody(req);
          try { return send(200,await spatial.update(spatialUpdate[1],body)); }
          catch(e) { if(e instanceof ServiceError)throw e; error(400,'INVALID_ROUTE',e.message); }
        }
        if (method === 'GET' && pathname === '/api/health') return send(200, { ok:true, mode:'local', externalDelivery:false });
        if (method === 'GET' && pathname === '/api/catalog') return send(200, await getCatalog());
        if (pathname === '/api/incidents' && method === 'GET') return send(200, { incidents: await store.list() });
        if (pathname === '/api/incidents' && method === 'POST') return send(201, await store.create(await jsonBody(req)));
        const incident = /^\/api\/incidents\/(inc-[a-f0-9-]{36})(?:\/(messages|decision))?$/.exec(pathname);
        if (incident) {
          if (method === 'GET' && !incident[2]) return send(200, await store.get(incident[1]));
          if (method === 'PATCH' && !incident[2]) return send(200, await store.update(incident[1], await jsonBody(req)));
          if (method === 'POST' && incident[2] === 'messages') return send(200, await store.message(incident[1], await jsonBody(req)));
          if (method === 'POST' && incident[2] === 'decision') return send(200, await store.decide(incident[1], await jsonBody(req)));
          error(405, 'METHOD_NOT_ALLOWED', 'Method not allowed for this endpoint.');
        }
        const packet = /^\/api\/packets\/(pkt-[a-f0-9-]{36})$/.exec(pathname);
        if (method === 'GET' && packet) return send(200, await store.packet(packet[1]));
        error(404, 'NOT_FOUND', 'API endpoint not found.');
      }
      if (!['GET','HEAD'].includes(method)) error(405, 'METHOD_NOT_ALLOWED', 'Only GET and HEAD are supported for files.');
      if (pathname === '/') pathname = '/live.html';
      const isViewer = /^\/vendor\/matterport\/[\w./-]+$/.test(pathname);
      const allowed = isViewer || /^\/(?:live(?:-[\w-]+)?\.(?:html|js|css)|src\/(?:live[\w-]*\.(?:js|css)|tokens\.css)|assets\/[\w./-]+|data\/[\w./-]+\.json|qa\/(?:[\w-]+\.(?:webm|pdf|png|jpg)|demo-[\w-]+\.(?:mp4|vtt|html|css|txt|json)))$/.test(pathname);
      if (!allowed || pathname.split('/').some(part => part.startsWith('.'))) error(404, 'NOT_FOUND', 'File not found.');
      let filename, contents;
      try {
        filename = await realpath(resolve(root, `.${pathname}`));
        const actualRoot = await canonicalRoot;
        if (!filename.startsWith(actualRoot + sep)) error(404, 'NOT_FOUND', 'File not found.');
        const actual = filename.slice(actualRoot.length).split(sep).join('/');
        if (actual !== pathname) error(404, 'NOT_FOUND', 'File not found.');
        if (!mime[extname(filename)]) error(404, 'NOT_FOUND', 'File not found.');
        contents = await readFile(filename);
      } catch (e) { if (e instanceof ServiceError) throw e; error(404, 'NOT_FOUND', 'File not found.'); }
      res.writeHead(200, { ...securityHeaders, ...(isViewer ? {'Content-Security-Policy':viewerCSP,'Cache-Control':extname(filename)==='.html'?'no-store':'public, max-age=3600'} : {}), 'Content-Type':mime[extname(filename)], 'Content-Length':contents.length });
      res.end(method === 'HEAD' ? undefined : contents);
    } catch (e) {
      const status = e instanceof ServiceError ? e.status : 500;
      if (status === 500) console.error('Local service operation failed:', e.code || e.name);
      if (!res.headersSent) send(status, { error:{ code:e instanceof ServiceError ? e.code : 'INTERNAL_ERROR', message:e instanceof ServiceError ? e.message : 'The local save failed. Your previous saved revision is unchanged; retry after checking disk access.' } });
      else res.end();
    }
  });
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  return server;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 4175);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be between 1 and 65535.');
  const server = createLocalServer();
  server.listen(port, '127.0.0.1', () => console.log(`Pinpoint real-data console: http://127.0.0.1:${port}`));
  server.on('error', e => { console.error(e.message); process.exitCode = 1; });
}
