import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { planRoute, routeDirections, PRINCETON_ID } from '../src/live-route-model.js';
export { PRINCETON_ID };
export const princetonProperty={id:PRINCETON_ID,name:'Rabinowitz Hall · Princeton',address:'Princeton University, Princeton, NJ 08544',city:'Princeton',state:'NJ',postalCode:'08544',useType:'University',stories:3,source:{file:'Matterport public scan',recordUrl:'/api/spatial/model'},models:[{id:'P3S6VUTP3qB',name:'Princeton · Rabinowitz Hall',url:'https://my.matterport.com/show?m=P3S6VUTP3qB',joinStatus:'verified',images:[]}],unknowns:['Exact room and floor','Entry and route','Hazards on scene']};
export function spatialService(root,store){
  // A graph exported from the team's building graphs (data/<propertyId>-scan.json) wins
  // over the two hand-annotated graphs.
  const model=async(propertyId=PRINCETON_ID)=>{
    const names=[];
    if(/^[A-Za-z0-9-]{1,64}$/.test(String(propertyId)))names.push(`${propertyId}-scan.json`);
    if(propertyId===PRINCETON_ID)names.push('princeton-scan.json');
    if(propertyId==='5956257')names.push('los-angeles-scan.json');
    for(const name of names){
      try{return JSON.parse(await readFile(resolve(root,'data',name),'utf8'));}
      catch(e){if(e.code!=='ENOENT')throw e;}
    }
    throw new Error('No spatial model is mapped for this property.');
  };
  return {
    async config(propertyId=PRINCETON_ID){
      const env=await readFile(resolve(root,'.env.local'),'utf8').catch(()=> '');
      const key=process.env.MATTERPORT_SDK_KEY || env.match(/^MATTERPORT_SDK_KEY=([^\r\n]*)/m)?.[1]?.trim() || '';
      const graph=await model(propertyId);
      return {modelId:graph.modelId,sdkKey:key,bundlePath:'/vendor/matterport/showcase.html',model:graph};
    },model,
    async update(id,body){
      if(!body||typeof body!=='object'||Array.isArray(body))throw new Error('A route update object is required.');
      const keys=['expectedRevision','profile','entryId','destination','blockedEdges','nodeIds','connectors','updatedBy','reason','avoidElevators'];
      if(Object.keys(body).some(k=>!keys.includes(k)))throw new Error('Unknown route update field.');
      const i=await store.get(id);
      const g=await model(i.propertyId);
      // Carry the saved route's choices forward only if they still exist in this graph
      // (the graph file may have been replaced since the route was saved).
      const saved=i.spatialRoute, fits=saved&&g.entries.some(e=>e.id===saved.entryId)&&g.nodes.some(n=>n.id===saved.destination)&&(saved.blockedEdges||[]).every(id=>g.edges.some(e=>e.id===id)||/^connector-/.test(id));
      const route=planRoute(g,{...(fits ? {profile:saved.profile,entryId:saved.entryId,destination:saved.destination,blockedEdges:saved.blockedEdges,connectors:saved.connectors,avoidElevators:saved.avoidElevators??true}:{}),...body});
      return store.setRoute(id,body.expectedRevision,route,routeDirections(g,route),g.nodes.find(n=>n.id===route.destination).label);
    },
  };
}
export const viewerCSP="default-src 'self' https://*.matterport.com; script-src 'self' blob: 'unsafe-inline' 'unsafe-eval' https://*.matterport.com; style-src 'self' 'unsafe-inline' https://*.matterport.com; img-src 'self' data: blob: https://*.matterport.com; connect-src 'self' https://*.matterport.com wss://*.matterport.com; worker-src 'self' blob:; font-src 'self' data: https://*.matterport.com; media-src 'self' blob: https://*.matterport.com; frame-src 'self' https://*.matterport.com; object-src 'none'; base-uri 'self'; frame-ancestors 'self'";
