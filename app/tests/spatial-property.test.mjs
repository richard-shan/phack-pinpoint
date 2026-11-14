import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spatialService,PRINCETON_ID} from '../lib/spatial-service.mjs';
import {aerialPoint} from '../src/live-spatial.js';

test('route updates and config select the incident property graph, never Princeton by default',async t=>{
 const root=await mkdtemp(join(tmpdir(),'spatial-property-'));t.after(()=>rm(root,{recursive:true,force:true}));await mkdir(join(root,'data'));
 const make=modelId=>({modelId,defaultDestination:'b',entries:[{id:'entry',label:'Demo entry',nodeId:'a',position:{x:0,y:0,z:0}}],nodes:[{id:'a',label:'Start',floor:0,position:{x:0,y:0,z:0}},{id:'b',label:'Finish',floor:0,position:{x:4,y:0,z:0}}],edges:[{id:'ab',a:'a',b:'b',kind:'walk'}]});
 await writeFile(join(root,'data/princeton-scan.json'),JSON.stringify(make('princeton-model')));
 await writeFile(join(root,'data/los-angeles-scan.json'),JSON.stringify(make('la-model')));
 let propertyId='5956257';const service=spatialService(root,{get:async()=>({propertyId}),setRoute:async(id,revision,route)=>route});
 assert.equal((await service.config('5956257')).modelId,'la-model');assert.equal((await service.config()).modelId,'princeton-model');
 assert.equal((await service.update('incident',{expectedRevision:1})).modelId,'la-model');
 propertyId=PRINCETON_ID;assert.equal((await service.update('incident',{expectedRevision:1})).modelId,'princeton-model');
 propertyId='unknown';await assert.rejects(service.update('incident',{}),/No spatial model/);
});
test('aerial overlays use the selected property bounds',()=>{
 const b={west:-118.3,east:-118.2,south:34,north:34.1};
 const nw=aerialPoint({lat:b.north,lng:b.west},b),se=aerialPoint({lat:b.south,lng:b.east},b),center=aerialPoint({lat:34.05,lng:-118.25},b);
 assert.deepEqual(nw,[0,0]);assert.equal(se[0],1000);assert.ok(Math.abs(center[0]-500)<1e-8);assert.ok(Math.abs(center[1]-se[1]/2)<1e-8);
});
