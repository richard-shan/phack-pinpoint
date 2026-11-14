import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

// Exercise the actual navigation handler with a minimal DOM and a persistent API double.
const source = await readFile(new URL('../src/live.js', import.meta.url), 'utf8');
const navigateSource = source.slice(source.indexOf('async function navigate()'), source.indexOf('function startPoll()'));

test('Princeton retries a failed route without duplicating the incident or replacing an existing route', async () => {
  let saved, creates = 0, routes = 0, assetsReady = false;
  const main = { innerHTML: '', setAttribute() {}, removeAttribute() {} };
  const context = {
    main, location: { hash: '#princeton' }, window: { scrollTo() {} },
    document: { querySelector() { return null; }, querySelectorAll() { return []; } },
    aerialView: null, spatialView: null, routeVersion: 0, polling: null,
    chosenImage: 0, snapshot: null, incident: null, property: null,
    PRINCETON_ID: 'princeton', LA_DEMO_ID: 'la', clearInterval() {},
    loadingView: () => 'Loading', e: String,
    async api(path, method = 'GET', body) {
      if (path === '/incidents' && method === 'GET') return { incidents: saved ? [structuredClone(saved)] : [] };
      if (path === '/incidents' && method === 'POST') {
        creates++;
        saved = { id: 'inc-demo', propertyId: body.propertyId, revision: 1 };
        return structuredClone(saved);
      }
      assert.equal(path, '/incidents/inc-demo/route');
      assert.equal(method, 'POST');
      assert.equal(body.expectedRevision, saved.revision);
      routes++;
      if (!assetsReady) throw new Error('Scan graph is missing');
      saved = { ...saved, revision: 2, spatialRoute: { destination: 'gallery' } };
      return structuredClone(saved);
    },
  };
  const navigate = runInNewContext(`${navigateSource}\nnavigate`, context);
  await navigate();
  assert.match(main.innerHTML, /Scan graph is missing/);
  assert.equal(creates, 1);
  assert.equal(routes, 1);

  assetsReady = true;
  await navigate();
  assert.equal(creates, 1);
  assert.equal(routes, 2);
  assert.equal(context.location.hash, 'incident/inc-demo');

  saved.status = 'approved';
  const approved = structuredClone(saved);
  context.location.hash = '#princeton';
  await navigate();
  assert.equal(routes, 2);
  assert.deepEqual(saved, approved);
});
