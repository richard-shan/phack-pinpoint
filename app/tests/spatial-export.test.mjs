import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spatialService} from '../lib/spatial-service.mjs';
import {createLocalServer} from '../local-server.mjs';

const make=(modelId,entry='entry')=>({modelId,defaultDestination:'b',entries:[{id:entry,label:'Door',nodeId:'a',position:{x:0,y:0,z:0},approach:[{lat:0,lng:0}]}],nodes:[{id:'a',label:'Start',floor:0,position:{x:0,y:0,z:0}},{id:'b',label:'Finish',floor:0,position:{x:4,y:0,z:0}}],edges:[{id:'0-1',a:'a',b:'b',kind:'walk'}]});

test('a graph exported for a property id is used, and wins over the built-in demo graph',async t=>{
 const root=await mkdtemp(join(tmpdir(),'spatial-export-'));t.after(()=>rm(root,{recursive:true,force:true}));await mkdir(join(root,'data'));
 await writeFile(join(root,'data/los-angeles-scan.json'),JSON.stringify(make('hand-drawn')));
 await writeFile(join(root,'data/5956257-scan.json'),JSON.stringify(make('exported')));
 await writeFile(join(root,'data/6550930-scan.json'),JSON.stringify(make('fannin')));
 const service=spatialService(root,{get:async()=>({propertyId:'6550930'}),setRoute:async(id,revision,route)=>route});
 assert.equal((await service.config('5956257')).modelId,'exported');
 assert.equal((await service.config('6550930')).modelId,'fannin');
 assert.equal((await service.update('incident',{expectedRevision:1})).modelId,'fannin');
 await assert.rejects(service.model('../catalog'),/No spatial model/);
 await assert.rejects(service.model('350372'),/No spatial model/);
});

test('a saved route that no longer fits the graph does not block a new route',async t=>{
 const root=await mkdtemp(join(tmpdir(),'spatial-export-'));t.after(()=>rm(root,{recursive:true,force:true}));await mkdir(join(root,'data'));
 await writeFile(join(root,'data/5956257-scan.json'),JSON.stringify(make('exported','door_north_main')));
 const saved={profile:'foot',entryId:'south',destination:'gone',blockedEdges:['9-10'],connectors:[]};
 const service=spatialService(root,{get:async()=>({propertyId:'5956257',spatialRoute:saved}),setRoute:async(id,revision,route)=>route});
 const route=await service.update('incident',{expectedRevision:1,profile:'foot'});
 assert.equal(route.status,'ready');assert.equal(route.entryId,'door_north_main');assert.equal(route.destination,'b');
});

test('the catalog marks properties that have an exported graph, and unknown ones answer 404',async t=>{
 const root=await mkdtemp(join(tmpdir(),'spatial-export-'));await mkdir(join(root,'data'));
 await writeFile(join(root,'data/catalog.json'),JSON.stringify({properties:[{id:'6550930',name:'A'},{id:'350372',name:'B'}]}));
 await writeFile(join(root,'data/6550930-scan.json'),JSON.stringify(make('fannin')));
 const server=createLocalServer({root,stateDirectory:join(root,'state')});await new Promise(r=>server.listen(0,'127.0.0.1',r));
 t.after(async()=>{await new Promise(r=>server.close(r));await rm(root,{recursive:true,force:true});});
 const origin=`http://127.0.0.1:${server.address().port}`;
 const catalog=await (await fetch(origin+'/api/catalog')).json();
 assert.equal(catalog.properties.find(p=>p.id==='6550930').spatial,true);
 assert.equal(catalog.properties.find(p=>p.id==='350372').spatial,undefined);
 assert.equal((await fetch(origin+'/api/spatial/model?propertyId=6550930')).status,200);
 const missing=await fetch(origin+'/api/spatial/model?propertyId=350372');
 assert.equal(missing.status,404);assert.equal((await missing.json()).error.code,'NO_SPATIAL_MODEL');
});
