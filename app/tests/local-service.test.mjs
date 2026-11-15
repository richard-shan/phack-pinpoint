import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { request } from 'node:http';
import { createLocalServer } from '../local-server.mjs';

const property = { id:'23553', name:'Source building', address:'Verified source address', unknowns:['Contact is not confirmed.'] };
async function fixture(t, options={}) {
  const root = await mkdtemp(join(tmpdir(), 'pinpoint-service-'));
  await mkdir(join(root,'data/records'), { recursive:true });
  await mkdir(join(root,'assets'));
  await writeFile(join(root,'data/catalog.json'), JSON.stringify({ properties:[property] }));
  await writeFile(join(root,'live.html'), '<!doctype html><title>Local test</title>');
  await writeFile(join(root,'data/records/23553.json'), JSON.stringify(property));
  let server = createLocalServer({ root,...options });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  let origin = `http://127.0.0.1:${server.address().port}`;
  t.after(async () => { await new Promise(resolve => server.close(resolve)); await rm(root,{recursive:true,force:true}); });
  return {
    root, get origin(){ return origin; },
    async restart(){ await new Promise(resolve => server.close(resolve)); server = createLocalServer({root}); await new Promise(resolve => server.listen(0,'127.0.0.1',resolve)); origin = `http://127.0.0.1:${server.address().port}`; },
    async api(path, method='GET', body, headers={}) {
      const response = await fetch(origin + path, { method, headers:{ ...(body === undefined ? {} : {'Content-Type':'application/json'}), ...headers }, body:body === undefined ? undefined : JSON.stringify(body) });
      return { status:response.status, body:await response.json(), headers:response.headers };
    },
  };
}
const create = f => f.api('/api/incidents','POST',{ propertyId:'23553', callerText:'I am beside the reception desk.' });

test('photo inference creates an incident with the original caller text and rejects invalid or uncertain photos', async t => {
  const f=await fixture(t,{inferBuilding:async()=>({building_id:'23553'})});
  const photo={name:'caller.jpg',dataUrl:'data:image/jpeg;base64,/9j/2Q=='};
  const result=await f.api('/api/demo-photo','POST',{photo,callerText:'I am by the stairs'});
  assert.equal(result.status,201);
  assert.equal(result.body.callerText,'I am by the stairs');
  assert.equal(result.body.messages[0].text,'I am by the stairs');
  assert(result.body.events.some(e=>e.text.includes('dispatcher confirmation required')));
  assert.equal((await f.api('/api/demo-photo','POST',{photo:{...photo,dataUrl:'data:image/jpeg;base64,YWJjZA=='}})).status,400);
  const uncertain=await fixture(t,{inferBuilding:async()=>({building_id:null})});
  assert.equal((await uncertain.api('/api/demo-photo','POST',{photo})).status,422);
  assert.equal((await uncertain.api('/api/incidents')).body.incidents.length,0);
});

test('local intake persists across restarts with explicit unknowns and no synthesized route', async t => {
  const f = await fixture(t); const result = await create(f);
  assert.equal(result.status,201); const incident = result.body;
  assert.equal(incident.revision,1); assert.equal(incident.location,''); assert.equal(incident.route,'');
  assert(incident.unconfirmed.includes('Contact is not confirmed.'));
  assert.equal(incident.messages[0].role,'caller');
  await f.restart();
  assert.deepEqual((await f.api(`/api/incidents/${incident.id}`)).body,incident);
  assert.equal((await f.api('/api/incidents')).body.incidents[0].id,incident.id);
  assert.equal((await f.api('/api/health')).body.externalDelivery,false);
});

test('approval snapshots remain unchanged after source changes, edits and subsequent approvals', async t => {
  const f = await fixture(t); const {body:initial} = await create(f);
  const path = `/api/incidents/${initial.id}`;
  const approved = await f.api(path+'/decision','POST',{ expectedRevision:1, decision:'approved' });
  assert.equal(approved.status,200); assert.equal(approved.body.decision.revision,1);
  const firstSnapshot = (await f.api('/api/packets/'+approved.body.packetId)).body;
  assert.equal(firstSnapshot.property.name,property.name);
  await writeFile(join(f.root,'data/catalog.json'),JSON.stringify({properties:[{...property,name:'Changed source name'}]}));
  const edited = await f.api(path,'PATCH',{expectedRevision:1,notes:'New dispatcher note',location:'Caller-reported reception desk'});
  assert.equal(edited.body.status,'review'); assert.equal(edited.body.decision,null); assert.equal(edited.body.packetId,null); assert.equal(edited.body.revision,2);
  assert.deepEqual((await f.api('/api/packets/'+firstSnapshot.id)).body,firstSnapshot);
  assert.equal((await f.api(path+'/decision','POST',{expectedRevision:1,decision:'approved'})).status,409);
  const newApproval = await f.api(path+'/decision','POST',{expectedRevision:2,decision:'approved'});
  const second = (await f.api('/api/packets/'+newApproval.body.packetId)).body;
  assert.notEqual(second.id,firstSnapshot.id); assert.equal(second.property.name,'Changed source name');
  await f.restart(); assert.deepEqual((await f.api('/api/packets/'+firstSnapshot.id)).body,firstSnapshot);
});

test('concurrent mutations enforce expectedRevision rather than losing a message or edit', async t => {
  const f=await fixture(t); const {body:i}=await create(f); const path=`/api/incidents/${i.id}`;
  const results=await Promise.all([
    f.api(path,'PATCH',{expectedRevision:1,notes:'A'}),
    f.api(path+'/messages','POST',{expectedRevision:1,role:'caller',text:'B'}),
  ]);
  assert.deepEqual(results.map(r=>r.status).sort(),[200,409]);
  assert.equal((await f.api(path)).body.revision,2);
});

test('rejection requires a reason, and a new local caller reply invalidates approval', async t => {
  const f=await fixture(t); const {body:i}=await create(f); const path=`/api/incidents/${i.id}`;
  assert.equal((await f.api(path+'/decision','POST',{expectedRevision:1,decision:'rejected'})).status,400);
  assert.equal((await f.api(path+'/decision','POST',{expectedRevision:1,decision:'rejected',reason:'Need floor confirmation'})).body.status,'rejected');
  await f.restart();
  assert.equal((await f.api(path)).body.status,'rejected');
  assert.equal((await f.api(path+'/decision','POST',{expectedRevision:1,decision:'approved'})).status,409);
  const reply=await f.api(path+'/messages','POST',{expectedRevision:1,role:'caller',text:'Second floor'});
  assert.equal(reply.body.status,'review'); assert.equal(reply.body.revision,2);
  const approved=await f.api(path+'/decision','POST',{expectedRevision:2,decision:'approved'});
  assert.equal(approved.body.status,'approved');
  const correction=await f.api(path+'/messages','POST',{expectedRevision:2,role:'dispatcher',text:'Please confirm suite number.'});
  assert.equal(correction.body.decision,null); assert.equal(correction.body.packetId,null);
});

test('invalid records, uploads and oversized requests cannot mutate state', async t => {
  const f=await fixture(t);
  assert.equal((await f.api('/api/incidents','POST',{propertyId:'not-real',callerText:'hello'})).status,400);
  assert.equal((await f.api('/api/incidents','POST',{propertyId:'23553',callerText:''})).status,400);
  assert.equal((await f.api('/api/incidents','POST',{propertyId:'23553',callerText:'hello',status:'approved'})).status,400);
  assert.equal((await f.api('/api/incidents','POST',{propertyId:'23553',photo:{name:'fake.png',dataUrl:'data:image/png;base64,SGVsbG8='}})).status,400);
  const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6VQAAAABJRU5ErkJggg==';
  const result=await f.api('/api/incidents','POST',{propertyId:'23553',photo:{name:'pixel.png',dataUrl:png}});
  assert.equal(result.status,201); assert.equal(result.body.photo.dataUrl,png);
  const replaced=await f.api('/api/incidents/'+result.body.id,'PATCH',{expectedRevision:1,photo:{name:'followup.png',dataUrl:png},packetMarkdown:'x'.repeat(102400)});
  assert.equal(replaced.status,200); assert.equal(replaced.body.photo.name,'followup.png'); assert.equal(replaced.body.packetMarkdown.length,102400);
  assert.equal((await f.api('/api/incidents/'+result.body.id,'PATCH',{expectedRevision:2,photo:{name:'bad.png',dataUrl:'data:image/png;base64,SGVsbG8='}})).status,400);
  assert.equal((await f.api('/api/incidents/'+result.body.id)).body.revision,2);
  const oversized=await f.api('/api/incidents','POST',{propertyId:'23553',callerText:'a'.repeat(3*1024*1024)});
  assert.equal(oversized.status,413);
  const broken=await fetch(f.origin+'/api/incidents',{method:'POST',headers:{'Content-Type':'application/json'},body:'{'});
  assert.equal(broken.status,400);
  assert.equal((await f.api('/api/incidents','POST',{propertyId:'23553',callerText:'hello'},{'Content-Type':'text/plain'})).status,415);
  assert.equal((await f.api('/api/incidents')).body.incidents.length,1);
});

test('loopback service rejects cross-origin writes and hides state and non-browser files', async t => {
  const f=await fixture(t);
  const denied=await f.api('/api/incidents','POST',{propertyId:'23553',callerText:'hello'},{Origin:'https://outside.invalid'});
  assert.equal(denied.status,403);
  assert.equal((await f.api('/api/catalog','GET',undefined,{'Sec-Fetch-Site':'cross-site'})).status,403);
  const badHost=await new Promise((resolve,reject)=>{
    const req=request(f.origin+'/api/health',{headers:{Host:'outside.invalid'}},res=>{res.resume();res.on('end',()=>resolve(res.statusCode));});req.on('error',reject);req.end();
  });
  assert.equal(badHost,403);
  for(const path of ['/.local-state/test.json','/local-server.mjs','/lib/local-store.mjs','/README.md','/data/.hidden.json','/src/adapter.js']) assert.equal((await fetch(f.origin+path)).status,404,path);
  assert.equal((await fetch(f.origin+'/')).status,200);
  assert.equal((await fetch(f.origin+'/data/records/23553.json')).status,200);
  await symlink(join(f.root,'data/catalog.json'),join(f.root,'assets/symlink.json'));
  assert.equal((await fetch(f.origin+'/assets/symlink.json')).status,404);
});

test('agent route revisions persist, invalidate approval, reject stale updates and preserve approved geometry', async t => {
  const f=await fixture(t);
  const graph={modelId:'P3S6VUTP3qB',defaultDestination:'b',entries:[{id:'south',label:'South approach',nodeId:'a',position:{x:-1,y:0,z:0}}],nodes:[{id:'a',label:'Entry',floor:1,position:{x:0,y:0,z:0}},{id:'b',label:'Upper gallery',floor:2,position:{x:4,y:4,z:0}}],edges:[{id:'stairs',a:'a',b:'b',kind:'stairs'}]};
  await writeFile(join(f.root,'data/princeton-scan.json'),JSON.stringify(graph));
  let i=(await f.api('/api/incidents','POST',{propertyId:'princeton-rabinowitz',callerText:'Demo caller'})).body;
  const path=`/api/incidents/${i.id}`;
  let response=await f.api(path+'/route','POST',{expectedRevision:i.revision,updatedBy:'agent'});assert.equal(response.status,200);i=response.body;
  assert.equal(i.spatialRoute.revision,2);assert.equal(i.spatialRoute.updatedBy,'agent');
  const approved=(await f.api(path+'/decision','POST',{expectedRevision:i.revision,decision:'approved'})).body;
  response=await f.api(path+'/route','POST',{expectedRevision:1,profile:'stretcher'});assert.equal(response.status,409);
  response=await f.api(path+'/route','POST',{expectedRevision:i.revision,profile:'stretcher',updatedBy:'agent',reason:'Step-free request'});assert.equal(response.status,200);i=response.body;
  assert.equal(i.status,'review');assert.equal(i.packetId,null);assert.equal(i.spatialRoute.status,'unavailable');assert.deepEqual(i.spatialRoute.segments,[]);
  const snapshot=(await f.api('/api/packets/'+approved.packetId)).body;assert.equal(snapshot.incident.spatialRoute.status,'ready');
  await f.restart();assert.equal((await f.api(path)).body.spatialRoute.status,'unavailable');
  assert.equal((await f.api(path+'/route','POST',{expectedRevision:i.revision,nodeIds:['b','a']})).status,400);
});

test('top-level navigation from an external page is allowed while cross-site API reads stay blocked',async t=>{
 const f=await fixture(t);
 const status=await new Promise((resolve,reject)=>{const req=request(f.origin+'/live.html',{headers:{'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}},res=>{res.resume();resolve(res.statusCode);});req.on('error',reject);req.end();});
 assert.equal(status,200);
 assert.equal((await f.api('/api/catalog','GET',undefined,{'Sec-Fetch-Site':'cross-site'})).status,403);
});

test('Princeton imagery advertises expanded coverage without exposing credentials', async t => {
  const f=await fixture(t);
  const config=await f.api('/api/aerial/config');
  assert.equal(config.body.provider,'Vexcel');
  assert.equal(config.body.available,true);
  const image=await f.api('/api/aerial/princeton.jpg');
  assert.equal(image.status,503);
  assert.equal(image.body.error.code,'VEXCEL_KEY_MISSING');
  assert.ok(!image.headers.get('content-security-policy').includes('arcgisonline'));
});


test('aerial endpoint uses catalog coordinates, caches images, and rejects unknown properties',async t=>{
 let calls=0;
 const f=await fixture(t,{aerialFetch:async(url,options)=>{
  calls++;const body=JSON.parse(options.body);assert.ok(body.wkt.includes('-118.'));assert.equal(body.srid,4326);
  return new Response(new Uint8Array([255,216,255]),{headers:{'Content-Type':'image/jpeg'}});
 }});
 await writeFile(join(f.root,'.env.local'),'VEXCEL_API_KEY=test-only-secret');
 await writeFile(join(f.root,'data/catalog.json'),JSON.stringify({properties:[{...property,city:'Los Angeles',latitude:34.06722,longitude:-118.286416}]}));
 for(let n=0;n<2;n++){
  const response=await fetch(f.origin+'/api/aerial/23553.jpg');assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'image/jpeg');assert.equal((await response.arrayBuffer()).byteLength,3);
 }
 assert.equal(calls,1);
 assert.equal((await f.api('/api/aerial/unknown.jpg')).status,404);
});
