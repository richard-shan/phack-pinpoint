import {routeComponentFactory} from './route-component.js';
const post = payload => window.webkit?.messageHandlers?.responder?.postMessage(payload);
let sdk, graph, route, component, scene, index = 0, busy = false, aligned = false, playing = false, initializing = false, ready = false, failed = false, onRoute = true, mode = 'dollhouse';
const subscriptions = [];
const frame = document.querySelector('#scan');
function report(extra = {}) {
  const node = graph?.nodes.find(n => n.id === route?.nodeIds[index]);
  post({type:'state', ready, busy, index, onRoute, total:route?.nodeIds.length || 0, mode,
    label:node?.label || '', floor:(node?.floor ?? 0) + 1, ...extra});
}
function fail(message) { failed = true; ready = false; busy = false; post({type:'error', message}); try { if(component) component.inputs.route = null; } catch {} }
async function initialize() {
  if (!aligned || !playing || initializing || ready || failed) return;
  initializing = true;
  busy = true;
  component.inputs.route = route;
  await sdk.Mode.moveTo(sdk.Mode.Mode.DOLLHOUSE);
  await sdk.Floor.showAll();
  if (failed) return;
  ready = true; busy = false;
  clearTimeout(timeout);
  report();
}
async function moveTo(next, reducedMotion) {
  const path = route.nodeIds;
  const at = id => graph.nodes.find(n => n.id === id).position;
  const a = at(path[next]), b = at(path[Math.min(next + 1, path.length - 1)]);
  const previous = next === path.length - 1 ? at(path[next - 1]) : a;
  const target = next === path.length - 1 ? a : b;
  const rotation = {x:-18, y:Math.atan2(-(target.x-previous.x), -(target.z-previous.z))*180/Math.PI};
  await sdk.Sweep.moveTo(path[next], {rotation,
    transition:reducedMotion ? sdk.Camera.TransitionType.INSTANT : sdk.Camera.TransitionType.FLY, transitionTime:650});
  index = next; onRoute = true; mode = 'inside';
}
window.responder = {
  async command(action, reducedMotion = false) {
    if (!ready || busy || failed) return;
    busy = true; report();
    const operationTimeout = setTimeout(() => fail('The scan stopped responding. Retry to reload the building.'), 20000);
    try {
      if (action === 'dollhouse') { await sdk.Mode.moveTo(sdk.Mode.Mode.DOLLHOUSE, {transition: reducedMotion ? sdk.Camera.TransitionType.INSTANT : sdk.Camera.TransitionType.FLY}); await sdk.Floor.showAll(); mode = 'dollhouse'; }
      if (action === 'inside') await moveTo(index, reducedMotion);
      if (action === 'next') await moveTo(Math.min(index + 1, route.nodeIds.length - 1), reducedMotion);
      if (action === 'previous') await moveTo(Math.max(0, index - 1), reducedMotion);
    } catch { post({type:'actionError', message:'Could not move to that view. Try again.'}); }
    finally { clearTimeout(operationTimeout); busy = false; report(); }
  }
};
const timeout = setTimeout(() => fail('The building scan could not finish loading. Check your internet connection and retry.'), 60000);
async function main() {
  const response = await fetch('config.json');
  if (!response.ok) throw Error('The bundled route is unavailable. Rebuild the app.');
  const config = await response.json(); graph = config.graph; route = config.route;
  frame.addEventListener('load', async () => {
    try {
      sdk = await frame.contentWindow.MP_SDK.connect(frame.contentWindow);
      await sdk.Scene.register('pinpoint.route', routeComponentFactory);
      [scene] = await sdk.Scene.createObjects(1);
      component = scene.addNode().addComponent('pinpoint.route', {route:null, floor:'all'}); scene.start();
      subscriptions.push(sdk.Sweep.data.subscribe({onCollectionUpdated(collection) {
        const sweeps = new Map(Object.values(collection).map(s => [s.id, s]));
        const nodes = route.nodeIds.map(id => graph.nodes.find(n => n.id === id));
        const errors = nodes.map(n => { const p = sweeps.get(n.id)?.puckPosition; return p ? Math.hypot(p.x-n.position.x,p.y-n.position.y,p.z-n.position.z) : null; });
        if(errors.some(e => e !== null && e > 0.3)) { fail('Scan alignment changed. Route arrows are hidden.'); return; }
        aligned = errors.every(e => e !== null);
        initialize().catch(() => fail('The dollhouse view could not open. Retry the scan.'));
      }}));
      subscriptions.push(sdk.App.state.subscribe(state => {
        if (state.phase === sdk.App.Phase.ERROR) fail('Matterport could not load this building. Check your connection and retry.');
        if (state.phase === sdk.App.Phase.PLAYING) { playing = true; initialize().catch(() => fail('The building view could not open. Retry the scan.')); }
      }));
      subscriptions.push(sdk.Mode.current.subscribe(value => {
        if(value === sdk.Mode.Mode.DOLLHOUSE || value === sdk.Mode.Mode.FLOORPLAN) mode = 'dollhouse';
        if(value === sdk.Mode.Mode.INSIDE) mode = 'inside';
        if(ready && !busy) report();
      }));
      subscriptions.push(sdk.Sweep.current.subscribe(sweep => {
        const routeIndex = route.nodeIds.indexOf(sweep.id);
        if(sweep.id && !busy) { onRoute = routeIndex >= 0; if(onRoute) index = routeIndex; if(ready) report(); }
      }));
      report();
    } catch { fail('The 3D viewer could not connect. Check the SDK key and retry.'); }
  }, {once:true});
  frame.src = `vendor/showcase.html?m=${encodeURIComponent(graph.modelId)}&applicationKey=${encodeURIComponent(config.sdkKey)}&play=1&title=0&brand=0&help=0&search=0&qs=0`;
}
main().catch(error => fail(error.message));
window.addEventListener('pagehide', () => { clearTimeout(timeout); subscriptions.forEach(s => s.cancel?.()); scene?.stop(); });
