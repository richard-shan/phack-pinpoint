import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { planRoute, routeDirections, PRINCETON_ID } from '../src/live-route-model.js';
export { PRINCETON_ID };
export const princetonProperty={id:PRINCETON_ID,name:'Rabinowitz Hall · Princeton',address:'Princeton University, Princeton, NJ 08544',city:'Princeton',state:'NJ',postalCode:'08544',useType:'University',stories:3,source:{file:'Matterport public scan',recordUrl:'/api/spatial/model'},models:[{id:'P3S6VUTP3qB',name:'Princeton · Rabinowitz Hall',url:'https://my.matterport.com/show?m=P3S6VUTP3qB',joinStatus:'verified',images:[]}],unknowns:['Demo caller destination is selected, not automatically localized.','Exterior entrance anchors and approach are illustrative and need on-site alignment.','Route graph is a demo interpretation of the scan, not a validated accessible or emergency route.','Elevator locations and current availability must be confirmed before adding a connection.']};
export function spatialService(root,store){
  const model=(propertyId=PRINCETON_ID)=>{
    const filename=propertyId===PRINCETON_ID?'princeton-scan.json':propertyId==='5956257'?'los-angeles-scan.json':null;
    if(!filename)throw new Error('No spatial model is mapped for this property.');
    return readFile(resolve(root,'data',filename),'utf8').then(JSON.parse);
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
      const keys=['expectedRevision','profile','entryId','destination','blockedEdges','nodeIds','connectors','updatedBy','reason'];
      if(Object.keys(body).some(k=>!keys.includes(k)))throw new Error('Unknown route update field.');
      const i=await store.get(id);
      const g=await model(i.propertyId);
      const route=planRoute(g,{...(i.spatialRoute ? {profile:i.spatialRoute.profile,entryId:i.spatialRoute.entryId,destination:i.spatialRoute.destination,blockedEdges:i.spatialRoute.blockedEdges,connectors:i.spatialRoute.connectors}:{}),...body});
      return store.setRoute(id,body.expectedRevision,route,routeDirections(g,route),g.nodes.find(n=>n.id===route.destination).label);
    },
  };
}
export const viewerCSP="default-src 'self' https://*.matterport.com; script-src 'self' blob: 'unsafe-inline' 'unsafe-eval' https://*.matterport.com; style-src 'self' 'unsafe-inline' https://*.matterport.com; img-src 'self' data: blob: https://*.matterport.com; connect-src 'self' https://*.matterport.com wss://*.matterport.com; worker-src 'self' blob:; font-src 'self' data: https://*.matterport.com; media-src 'self' blob: https://*.matterport.com; frame-src 'self' https://*.matterport.com; object-src 'none'; base-uri 'self'; frame-ancestors 'self'";
