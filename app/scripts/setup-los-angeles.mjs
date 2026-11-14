import {readFile,writeFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const env=await readFile(resolve(root,'.env.local'),'utf8');
const value=n=>process.env[n]||env.match(new RegExp(`^${n}=(.*)$`,'m'))?.[1]?.trim();
if(!value('MATTERPORT_API_KEY')||!value('MATTERPORT_API_TOKEN'))throw new Error('Configure Matterport API credentials in .env.local.');
const modelId='JUz7H4u8m5C';
const query=`{ model(id:"${modelId}") { id name geolocation { lat long } floors { id label } panoLocations { id label anchor { position { x y z } neighbors floor { id label } } } } }`;
const response=await fetch('https://api.matterport.com/api/models/graph',{method:'POST',headers:{Authorization:'Basic '+Buffer.from(value('MATTERPORT_API_KEY')+':'+value('MATTERPORT_API_TOKEN')).toString('base64'),'Content-Type':'application/json'},body:JSON.stringify({query})});
if(!response.ok)throw new Error(`Model API failed (${response.status})`);
const result=await response.json();if(result.errors)throw new Error('Model API could not return the required floor anchors.');
const m=result.data.model;
const nodes=m.panoLocations.map(p=>({id:p.id,index:Number(p.label),floor:m.floors.findIndex(f=>f.id===p.anchor.floor.id),position:{x:p.anchor.position.x,y:p.anchor.position.z,z:-p.anchor.position.y},label:`Lobby · scan ${p.label}`})).sort((a,b)=>a.index-b.index);
const byIndex=new Map(nodes.map(n=>[n.index,n])),raw=new Map(m.panoLocations.map(p=>[p.id,p]));
byIndex.get(0).label='Lobby boundary · illustrative entry';byIndex.get(10).label='Lobby · demo caller (scan 10)';
// Deliberate short same-level chains, not the API's dense visibility graph.
// These are demo annotations, not verified physical traversal or accessibility.
const chains=[[0,1,2],[1,3,4,5,6,7,8,9,10,11,12],[1,13,14,15,16,17,18,19,20,22,21]];
const edges=chains.flatMap(c=>c.slice(1).map((b,i)=>{
 const a=byIndex.get(c[i]),n=byIndex.get(b);
 if(!a||!n||a.floor!==n.floor||!raw.get(a.id).anchor.neighbors.includes(n.id)||Math.hypot(a.position.x-n.position.x,a.position.z-n.position.z)>10)throw new Error('Demo chain no longer matches the source scan. Reinspect before rebuilding.');
 return {id:`${Math.min(a.index,n.index)}-${Math.max(a.index,n.index)}`,a:a.id,b:n.id,kind:'walk',source:'illustrative-same-level-scan-chain'};
}));
const lat=34.06722,lng=-118.286416,dy=70/111320,dx=dy/Math.cos(lat*Math.PI/180);
const approach=[{lat:34.06672,lng:-118.28618},{lat:34.06688,lng:-118.28618},{lat:34.06701,lng:-118.28618}];
const data={modelId,faceRoute:true,supportsOverview:false,name:'3160 Geneva St · Los Angeles',georeference:m.geolocation,floors:m.floors.map((f,sequence)=>({...f,sequence})),nodes,edges,entries:[{id:'south',label:'South approach · illustrative',nodeId:byIndex.get(0).id,position:byIndex.get(0).position,...approach.at(-1),approach,calibration:'Illustrative exterior approach. The aerial anchor is not surveyed or registered to the indoor doorway.'}],defaultDestination:byIndex.get(10).id,aerial:{bounds:{west:lng-dx,south:lat-dy,east:lng+dx,north:lat+dy},url:'/api/aerial/5956257.jpg'},provenance:{url:`https://my.matterport.com/show?m=${modelId}`,retrieved:new Date().toISOString(),coordinates:'Model API anchor floor positions transformed from Z-up to SDK Y-up (x,z,-y). Camera positions are not used.',routing:'Illustrative same-level scan chains; no validated traversal, door clearance, or accessibility claim.',aerial:'Vexcel. Approach waypoints are illustrative and not registered to the scan entrance.'}};
await writeFile(resolve(root,'data/los-angeles-scan.json'),JSON.stringify(data,null,2)+'\n');
console.log(`Prepared ${nodes.length} floor anchors and ${edges.length} demo segments.`);
