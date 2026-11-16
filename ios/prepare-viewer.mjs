// Bundle the existing viewer and one deterministic route, without a dispatcher server.
import {cp, mkdir, readFile, writeFile} from 'node:fs/promises';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {planRoute} from '../app/src/live-route-model.js';
import {routeComponentFactory} from '../app/src/live-spatial.js';
const here = dirname(fileURLToPath(import.meta.url));
const target = resolve(process.argv[2] || resolve(here, 'Pinpoint/ViewerBundle'));
const graph = JSON.parse(await readFile(resolve(here, '../app/data/10352309-scan.json'), 'utf8'));
// Same-level conference-room route: real captured positions, illustrative incident.
const destination = 'e62ehd7a7ktnyf7z1yw4hq61b';
const route = planRoute(graph, {destination, profile:'foot'});
if (route.status !== 'ready' || route.nodeIds.length < 2) throw Error('Sample route must be connected.');
const env = await readFile(resolve(here, '../app/.env.local'), 'utf8').catch(() => '');
const keyLines = (await readFile(resolve(here, '../api.txt'), 'utf8').catch(() => '')).split(/\r?\n/).map(line => line.trim());
const sdkLine = keyLines.indexOf('SDK key');
const sdkKey = process.env.MATTERPORT_SDK_KEY || env.match(/^MATTERPORT_SDK_KEY=([^\r\n]*)/m)?.[1]?.trim() || (sdkLine >= 0 ? keyLines[sdkLine + 1] : '');
if (!sdkKey) throw Error('Set MATTERPORT_SDK_KEY in app/.env.local before building the scan demo.');
await mkdir(target, {recursive:true});
await cp(resolve(here, '../app/vendor/matterport'), resolve(target, 'vendor'), {recursive:true});
await cp(resolve(here, 'ViewerSource'), target, {recursive:true});
await writeFile(resolve(target, 'route-component.js'), `// Shared verbatim with the dispatcher viewer.\nexport const routeComponentFactory = ${routeComponentFactory.toString()};\n`);
await writeFile(resolve(target, 'config.json'), JSON.stringify({sdkKey, graph, route}));
console.log(`Bundled Matterport viewer and ${route.nodeIds.length} route positions. No dispatcher connection required.`);
