// 3D route page: the scan's mesh, the floor image on each level, and the route to the caller.
// Data comes from two places the agent run writes: /assets/real/live/<incident>/route3d.json
// (the route, in the viewer's axes) and /assets/real/models/<building>/ (the model).
import * as THREE from '/assets/real/vendor/three.module.min.js';

const incidentId = location.hash.slice(1).replace(/[^\w-]/g, '');
if (new URLSearchParams(location.search).has('embed')) document.body.classList.add('embed');   // inside the console
const $ = id => document.getElementById(id);
const status = text => { $('m3-status').hidden = !text; $('m3-status').textContent = text || ''; };
const getJSON = async url => { const r = await fetch(url); if (!r.ok) throw new Error(`${url} (${r.status})`); return r.json(); };
const RED = 0xd62839, BLUE = 0x2962a0, GREEN = 0x26a05a;

async function main() {
  if (!incidentId) return status('Open this page from an incident in the console.');
  $('m3-back').href = `/live.html#incident/${incidentId}`;
  let route, model, mesh;
  try { route = await getJSON(`/assets/real/live/${incidentId}/route3d.json`); }
  catch { return status('No route has been computed for this incident yet.'); }
  try { model = await getJSON(route.model); mesh = await getJSON(model.mesh); }
  catch { return status('This building has no 3D model on this machine. Run: python -m live.model3d ' + route.buildingId); }
  $('m3-title').textContent = route.title || model.name;
  document.title = `${route.title || model.name} · 3D route · Pinpoint`;

  const canvas = $('m3-canvas');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.localClippingEnabled = true;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x14181f);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x3a4252, 1.15));
  const sun = new THREE.DirectionalLight(0xffffff, 0.9); sun.position.set(0.5, 1, 0.35); scene.add(sun);

  // ---- the building: front faces only, so ceilings and near walls drop away (a dollhouse)
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(mesh.positions), 3));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(mesh.indices), 1));
  geometry.computeVertexNormals();
  const clip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 1e6);
  const shell = new THREE.Mesh(geometry, new THREE.MeshLambertMaterial({ color: 0xe2e6ee, side: THREE.FrontSide, clippingPlanes: [clip] }));
  scene.add(shell);

  // ---- the floor image of each level, laid just above the floor
  const loader = new THREE.TextureLoader();
  const planes = [];
  for (const f of model.floors) {
    if (!f.plan) continue;
    const g = new THREE.BufferGeometry(), p = f.plan, y = f.height + 0.04;
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array([p.x0, y, p.z0, p.x0, y, p.z1, p.x1, y, p.z1, p.x1, y, p.z0]), 3));
    g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array([0, 1, 0, 0, 1, 0, 1, 1]), 2));
    g.setIndex([0, 1, 2, 0, 2, 3]);
    const texture = loader.load(p.url, () => draw()); texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = 8;
    const plane = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: texture, transparent: true, side: THREE.DoubleSide, depthWrite: false, clippingPlanes: [clip] }));
    plane.userData.floor = f.sequence; plane.renderOrder = 2; planes.push(plane); scene.add(plane);
  }

  // ---- the route: thick, red, and drawn through walls
  const lift = p => new THREE.Vector3(p[0], p[1] + 0.25, p[2]);
  const overlay = color => new THREE.MeshBasicMaterial({ color, depthTest: false, depthWrite: false, transparent: true });
  const routeGroup = new THREE.Group(); routeGroup.renderOrder = 20; scene.add(routeGroup);
  const tube = (a, b, radius, material) => {
    const dir = new THREE.Vector3().subVectors(b, a), len = dir.length(); if (len < 0.01) return;
    const m = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, len, 12), material);
    m.position.copy(a).addScaledVector(dir, 0.5); m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize()); m.renderOrder = 20; routeGroup.add(m);
  };
  const halo = overlay(0xffffff), core = overlay(RED), outside = overlay(0xf08a94);
  const joint = (p, r, material) => { const s = new THREE.Mesh(new THREE.SphereGeometry(r, 16, 12), material); s.position.copy(p); s.renderOrder = 20; routeGroup.add(s); };
  for (const leg of route.legs) { const a = lift(leg.a), b = lift(leg.b); tube(a, b, 0.2, halo); joint(a, 0.2, halo); joint(b, 0.2, halo); }
  for (const leg of route.legs) { const a = lift(leg.a), b = lift(leg.b), m = leg.outside ? outside : core; tube(a, b, 0.13, m); joint(a, 0.13, m); joint(b, 0.13, m); }
  // arrowheads along the way
  for (const leg of route.legs) {
    const a = lift(leg.a), b = lift(leg.b), dir = new THREE.Vector3().subVectors(b, a); if (dir.length() < 2.5) continue;
    const cone = new THREE.Mesh(new THREE.ConeGeometry(0.34, 0.8, 14), overlay(0xffffff));
    cone.position.copy(a).addScaledVector(dir, 0.55); cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize()); cone.renderOrder = 21; routeGroup.add(cone);
  }

  // ---- markers and their labels
  const labels = [];
  const legend = $('m3-legend');
  const names = { stage: 'Stage', enter: 'Enter', caller: 'Caller' }, colors = { stage: BLUE, enter: GREEN, caller: RED };
  for (const mk of route.markers) {
    const p = lift(mk.position), color = colors[mk.kind];
    let shape;
    if (mk.kind === 'enter') shape = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.8, 0.8), overlay(color));
    else shape = new THREE.Mesh(new THREE.SphereGeometry(mk.kind === 'caller' ? 0.5 : 0.45, 20, 14), overlay(color));
    shape.position.copy(p); shape.renderOrder = 22; routeGroup.add(shape);
    if (mk.kind === 'caller') {           // a beam, so the spot is visible from any side and through floors above
      const beamMaterial = overlay(color); beamMaterial.opacity = 0.35;
      const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, 7, 12), beamMaterial);
      beam.position.set(p.x, p.y + 3.5, p.z); beam.renderOrder = 19; routeGroup.add(beam);
      const ringMaterial = overlay(color); ringMaterial.side = THREE.DoubleSide;
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.75, 1.0, 40), ringMaterial);
      ring.rotation.x = -Math.PI / 2; ring.position.copy(p); ring.renderOrder = 22; routeGroup.add(ring);
    }
    const el = document.createElement('div'); el.className = `m3-label ${mk.kind}`; el.textContent = names[mk.kind];
    $('m3-labels').append(el); labels.push({ el, at: new THREE.Vector3(p.x, p.y + 0.9, p.z) });
    const li = document.createElement('li'), dot = document.createElement('span'), text = document.createElement('span');
    dot.className = `m3-dot ${mk.kind}`; const strong = document.createElement('strong'); strong.textContent = names[mk.kind] + ': ';
    text.append(strong, document.createTextNode(mk.label ? mk.label[0].toUpperCase() + mk.label.slice(1) : '')); li.append(dot, text); legend.append(li);
  }
  for (const line of route.lines || []) { const p = document.createElement('p'); p.textContent = line; $('m3-lines').append(p); }

  // ---- camera: orbit around what matters (the route), framed with the whole building in view
  const box = new THREE.Box3(new THREE.Vector3(...mesh.bounds.min), new THREE.Vector3(...mesh.bounds.max));
  for (const leg of route.legs) { box.expandByPoint(lift(leg.a)); box.expandByPoint(lift(leg.b)); }
  const centre = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 2000);
  const home = { azimuth: 0.6, elevation: 0.9, distance: Math.max(size.x, size.z, size.y * 2) * 0.92, target: centre.clone() };
  const view = { ...home, target: home.target.clone() };
  const place = () => {
    const e = Math.min(Math.max(view.elevation, 0.08), Math.PI / 2 - 0.001);
    camera.position.set(view.target.x + view.distance * Math.cos(e) * Math.sin(view.azimuth), view.target.y + view.distance * Math.sin(e), view.target.z + view.distance * Math.cos(e) * Math.cos(view.azimuth));
    camera.lookAt(view.target);
  };
  const draw = () => {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.floor(w * renderer.getPixelRatio()) || canvas.height !== Math.floor(h * renderer.getPixelRatio())) { renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
    place(); renderer.render(scene, camera);
    for (const l of labels) { const v = l.at.clone().project(camera); l.el.style.left = `${(v.x + 1) / 2 * w}px`; l.el.style.top = `${(1 - v.y) / 2 * h}px`; l.el.hidden = v.z > 1; }
  };
  let drag = null;
  canvas.addEventListener('pointerdown', ev => { drag = { x: ev.clientX, y: ev.clientY, pan: ev.shiftKey || ev.button === 2 }; canvas.setPointerCapture(ev.pointerId); canvas.classList.add('dragging'); });
  canvas.addEventListener('pointerup', () => { drag = null; canvas.classList.remove('dragging'); });
  canvas.addEventListener('contextmenu', ev => ev.preventDefault());
  canvas.addEventListener('pointermove', ev => {
    if (!drag) return; const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y; drag.x = ev.clientX; drag.y = ev.clientY;
    if (drag.pan) {
      const k = view.distance / canvas.clientHeight * 1.1, right = new THREE.Vector3(Math.cos(view.azimuth), 0, -Math.sin(view.azimuth)), fwd = new THREE.Vector3(Math.sin(view.azimuth), 0, Math.cos(view.azimuth));
      view.target.addScaledVector(right, -dx * k).addScaledVector(fwd, -dy * k);
    } else { view.azimuth -= dx * 0.006; view.elevation += dy * 0.005; }
    draw();
  });
  canvas.addEventListener('wheel', ev => { ev.preventDefault(); view.distance = Math.min(Math.max(view.distance * Math.exp(ev.deltaY * 0.0012), 3), home.distance * 4); draw(); }, { passive: false });
  window.addEventListener('resize', draw);
  new ResizeObserver(draw).observe(canvas);   // embedded: redraw when the frame is first sized or shown again
  $('m3-reset').addEventListener('click', () => { Object.assign(view, home, { target: home.target.clone() }); draw(); });
  $('m3-top').addEventListener('click', () => { view.elevation = Math.PI / 2; view.azimuth = 0; draw(); });

  // ---- levels: cut the model above the chosen floor so the levels below are in view
  const floors = [...model.floors].sort((a, b) => a.sequence - b.sequence);
  const showFloor = seq => {
    const i = floors.findIndex(f => f.sequence === seq);
    const top = i < 0 || i === floors.length - 1 ? 1e6 : (floors[i + 1].height - 0.3);
    clip.constant = top;
    for (const b of $('m3-floors').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.floor === String(seq)));
    draw();
  };
  if (floors.length > 1) {
    const add = (label, seq) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.dataset.floor = String(seq); b.addEventListener('click', () => showFloor(seq)); $('m3-floors').append(b); };
    add('All levels', 'all'); for (const f of floors) add(f.name + ' and below', f.sequence);
    const callerFloor = (route.markers.find(m => m.kind === 'caller') || {}).floor;
    showFloor(callerFloor === undefined ? 'all' : callerFloor);
  }
  status(''); draw();
  window.pinpoint3d = { view, draw, showFloor };            // for screenshots and tests
}
main().catch(error => status('The 3D view could not load: ' + error.message));
