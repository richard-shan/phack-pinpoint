import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {vexcelService,orthoRequest} from '../lib/vexcel-service.mjs';

test('Vexcel requests preserve longitude-first coordinates and keep credentials server-side',async t=>{
 const root=await mkdtemp(join(tmpdir(),'vexcel-'));t.after(()=>rm(root,{recursive:true,force:true}));
 await writeFile(join(root,'.env.local'),'VEXCEL_API_KEY=test-only-secret\n');
 let called=false;
 const service=vexcelService(root,async(url,options)=>{
  called=true;assert.equal(url,'https://api.vexcelgroup.com/v2/ortho/extract');assert.equal(options.method,'POST');assert.ok(options.headers['X-API-KEY']);
  const body=JSON.parse(options.body);assert.equal(body.wkt,'POLYGON((-119 34,-118 34,-118 35,-119 35,-119 34))');assert.equal(body.srid,4326);
  return new Response(new Uint8Array([255,216,255]),{headers:{'Content-Type':'image/jpeg'}});
 });
 assert.equal((await service.extract({west:-119,south:34,east:-118,north:35})).length,3);assert.ok(called);
 assert.equal(service.config().available,true);assert.ok(!JSON.stringify(service.config()).includes('secret'));
});
test('invalid geographic bounds fail before fetching',()=>{
 assert.throws(()=>orthoRequest({west:0,south:0,east:-1,north:1}),/Invalid aerial bounds/);
});
test('Vexcel access errors do not expose upstream credentials or bodies',async t=>{
 const root=await mkdtemp(join(tmpdir(),'vexcel-'));t.after(()=>rm(root,{recursive:true,force:true}));
 await writeFile(join(root,'.env.local'),'VEXCEL_API_KEY=test-only-secret\n');
 const service=vexcelService(root,async()=>new Response('sensitive upstream details',{status:403}));
 await assert.rejects(service.extract({west:-119,south:34,east:-118,north:35}),e=>e.status===403&&!e.message.includes('sensitive'));
});
