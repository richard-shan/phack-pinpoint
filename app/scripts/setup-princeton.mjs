// Rebuild local demo assets from the public tour and official SDK distribution.
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const modelId='P3S6VUTP3qB',source=`https://my.matterport.com/show?m=${modelId}`;
const response=await fetch(source);if(!response.ok)throw new Error(`Public scan fetch failed (${response.status})`);
const html=await response.text(),match=html.match(/MP_PREFETCHED_MODELDATA = parseJSON\((".*?")\);/s);
if(!match)throw new Error('Matterport bootstrap changed. Inspect the source before rebuilding.');
const q=JSON.parse(JSON.parse(match[1])).queries;
const m=q.GetModelPrefetch.data.model,r=q.GetRootPrefetch.data.model;
const locations=[...m.locations].sort((a,b)=>a.index-b.index);
const byIndex=new Map(locations.map(n=>[n.index,n]));
const nodes=locations.map(n=>({id:n.id,index:n.index,floor:n.floor.meshId,position:{x:n.position.x,y:n.position.z,z:-n.position.y},label:`Scan ${n.index} · ${n.floor.meshId===1?'Atrium level':'Upper gallery'}`}));
const named={25:'Northwest corridor · scan boundary',31:'Southwest corridor · scan boundary',35:'Main stair · lower landing',36:'Main stair · half landing',37:'Main stair · upper landing',55:'Upper gallery · demo caller'};
for(const n of nodes)if(named[n.index])n.label=named[n.index];
const chains=[[0,1,2,7,8,23,29,30,31],[0,4,33,3],[0,5,6,7],[5,9,10,24,25,26,28,29],[10,11,12,13,14,15,16,17,18],[18,19,20,21,22,32,31],[22,8],[6,27,28],[33,34,1],[35,8],[37,38,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,61,62,63,64,65,44],[38,39,40,41,59,56],[59,60,57],[41,42,43,40]];
const edge=(a,b,kind)=>({id:`${Math.min(a,b)}-${Math.max(a,b)}`,a:byIndex.get(a).id,b:byIndex.get(b).id,kind,source:kind==='stairs'?'visible-stair-flight':'demo-reviewed-corridor'});
const edges=chains.flatMap(c=>c.slice(1).map((b,i)=>edge(c[i],b,'walk'))).concat([edge(35,36,'stairs'),edge(36,37,'stairs')]);
const makeEntry=(id,label,index,approach)=>({id,label,nodeId:byIndex.get(index).id,position:nodes.find(n=>n.index===index).position,...approach.at(-1),approach,calibration:'Aerial anchor is illustrative. 3D route begins at the captured corridor boundary; the exterior doorway is not scanned.'});
const entries=[makeEntry('southwest','Southwest approach',31,[{lat:40.34873,lng:-74.65566},{lat:40.34887,lng:-74.65566},{lat:40.34894,lng:-74.65566}]),makeEntry('northwest','Northwest approach',25,[{lat:40.34940,lng:-74.65576},{lat:40.34922,lng:-74.65576},{lat:40.34922,lng:-74.65566}])];
const data={modelId,name:'Princeton · Rabinowitz Hall',georeference:m.geocoordinates,floors:r.floors.map(f=>({id:f.id,sequence:f.sequence,label:f.label})),nodes,edges,entries,defaultDestination:byIndex.get(55).id,provenance:{url:source,retrieved:new Date().toISOString().slice(0,10),coordinates:'Matterport location puck positions transformed from Z-up to SDK Y-up (x,z,-y).',routing:'Demonstration corridor chains, visually checked main stair; not a validated evacuation or accessibility graph.',aerial:'Vexcel sponsor aerial imagery; Princeton coverage expanded November 14. Approach waypoints are illustrative, not surveyed.'}};
await mkdir(resolve(root,'data'),{recursive:true});await writeFile(resolve(root,'data/princeton-scan.json'),JSON.stringify(data,null,2));
if(!process.argv.includes('--data-only')){
 const url='https://static.matterport.com/showcase-sdk/bundle/26.10.4_webgl-1728-g1cd7e0997a/showcase-bundle.zip';
 const res=await fetch(url);if(!res.ok)throw new Error(`Bundle download failed (${res.status})`);
 const dir=resolve(root,'vendor/matterport');await mkdir(dir,{recursive:true});
 const archive=resolve(dir,'showcase-bundle.zip');await writeFile(archive,Buffer.from(await res.arrayBuffer()));await promisify(execFile)('unzip',['-qo',archive,'-d',dir]);
}
console.log(`Prepared ${nodes.length} scan positions and ${edges.length} annotated edges. Keep local sponsor assets and credentials out of public repositories.`);
const env=await readFile(resolve(root,'.env.local'),'utf8').catch(()=> '');if(!/^MATTERPORT_SDK_KEY=.+/m.test(env)&&!process.env.MATTERPORT_SDK_KEY)console.log('Set MATTERPORT_SDK_KEY in app/.env.local, then run npm run dev:real and use localhost.');
