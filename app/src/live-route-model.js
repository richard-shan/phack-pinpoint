// Pure route planner shared by the service and its behavioral tests.
export const MODEL_ID = 'P3S6VUTP3qB';
export const PRINCETON_ID = 'princeton-rabinowitz';
// By default routes avoid elevators (they stall and get recalled in a fire): stairs only.
// The dispatcher can allow them per incident with the route view's Elevators toggle.
export const AVOID_ELEVATORS = true;
export const distance = (a,b) => Math.hypot(a.x-b.x,a.y-b.y,a.z-b.z);
export function planRoute(graph, request = {}) {
  const {profile='foot', entryId=graph.entries[0].id, destination=graph.defaultDestination, blockedEdges=[], nodeIds, connectors=[], avoidElevators=AVOID_ELEVATORS} = request;
  if (typeof avoidElevators !== 'boolean') throw new Error('avoidElevators must be true or false.');
  if (!['foot','stretcher'].includes(profile)) throw new Error('Choose foot or stretcher.');
  const nodes=new Map(graph.nodes.map(n=>[n.id,n]));
  const entry=graph.entries.find(e=>e.id===entryId);
  if(!entry || !nodes.has(destination)) throw new Error('Unknown entrance or destination.');
  if(!Array.isArray(blockedEdges) || blockedEdges.length>500 || blockedEdges.some(id=>typeof id!=='string')) throw new Error('Invalid blocked edges.');
  if(!Array.isArray(connectors)||connectors.length>20) throw new Error('Invalid floor connections.');
  const added=connectors.map((c,i)=>{
    if(!c || !nodes.has(c.a)||!nodes.has(c.b)||c.a===c.b||!['stairs','elevator'].includes(c.kind)||c.confirmed!==true) throw new Error('Floor connections need two scan positions, a type, and dispatcher confirmation.');
    return {id:`connector-${i}`,a:c.a,b:c.b,kind:c.kind,source:'dispatcher-mapped',points:c.kind==='elevator' ? [nodes.get(c.a).position,{...nodes.get(c.a).position,y:nodes.get(c.b).position.y},nodes.get(c.b).position] : [nodes.get(c.a).position,nodes.get(c.b).position]};
  });
  const allEdges=[...graph.edges,...added];
  if(blockedEdges.some(id=>!allEdges.some(e=>e.id===id))) throw new Error('Unknown blocked segment.');
  const usable=allEdges.filter(e=>!blockedEdges.includes(e.id)&&(profile!=='stretcher'||e.kind!=='stairs')&&!(avoidElevators&&e.kind==='elevator'));
  const adjacent=new Map(graph.nodes.map(n=>[n.id,[]]));
  for(const e of usable){const cost=distance(nodes.get(e.a).position,nodes.get(e.b).position)+(e.kind==='elevator'?15:0);adjacent.get(e.a).push({to:e.b,e,cost});adjacent.get(e.b).push({to:e.a,e,cost});}
  let path;
  if(nodeIds!==undefined){
    if(!Array.isArray(nodeIds)||nodeIds.length>500||!nodeIds.length||nodeIds[0]!==entry.nodeId||nodeIds.at(-1)!==destination||nodeIds.some(id=>!nodes.has(id))) throw new Error('Agent path must start at the entrance scan and end at the destination.');
    path=nodeIds;
    for(let i=1;i<path.length;i++) if(!adjacent.get(path[i-1]).some(e=>e.to===path[i]))throw new Error('Agent path crosses a blocked, unmapped, or incompatible segment.');
  } else {
    const costs=new Map([[entry.nodeId,0]]), previous=new Map(), queue=new Set([entry.nodeId]);
    while(queue.size){const here=[...queue].sort((a,b)=>costs.get(a)-costs.get(b))[0];queue.delete(here);if(here===destination)break;
      for(const {to,cost} of adjacent.get(here)){const next=costs.get(here)+cost;if(next<(costs.get(to)??Infinity)){costs.set(to,next);previous.set(to,here);queue.add(to);}}
    }
    if(costs.has(destination)){path=[destination];while(path[0]!==entry.nodeId)path.unshift(previous.get(path[0]));}
  }
  const result={modelId:graph.modelId,profile,entryId,destination,blockedEdges,connectors,entry,updatedBy:request.updatedBy==='agent'?'agent':'dispatcher',reason:typeof request.reason==='string'?request.reason.slice(0,500):'',avoidElevators,status:path?'ready':'unavailable',nodeIds:path||[],segments:[],meters:0};
  if(!path){result.message=profile==='stretcher'?(avoidElevators?'No stretcher route without elevators. Allow elevators, or carry from the entry level.':'No stretcher route to this location.'):'No route. Change the entry or clear a blocked segment.';return result;}
  result.segments.push({id:'entry',kind:'entry',floor:nodes.get(path[0]).floor,points:[entry.position,nodes.get(path[0]).position]});
  for(let i=1;i<path.length;i++){
    const a=nodes.get(path[i-1]),b=nodes.get(path[i]);const edge=adjacent.get(a.id).find(e=>e.to===b.id).e;
    let points=edge.points ? (edge.a===a.id?edge.points:[...edge.points].reverse()):[a.position,b.position];
    result.segments.push({id:edge.id,kind:edge.kind,floor:a.floor,toFloor:b.floor,points});
  }
  result.meters=Math.round(result.segments.reduce((sum,s)=>sum+s.points.slice(1).reduce((n,p,i)=>n+distance(s.points[i],p),0),0));
  result.message=`${result.meters} m inside · ${result.segments.some(s=>s.kind==='stairs')?'via main stair':result.segments.some(s=>s.kind==='elevator')?'via mapped elevator':'same level'}`;
  return result;
}
export function routeDirections(graph,route){
  if(route.status!=='ready')return route.message;
  const destination=graph.nodes.find(n=>n.id===route.destination);
  const name=label=>String(label||'').replace(/^Scan \d+ · /,'');
  return [`Enter at ${route.entry.label}.`, ...route.segments.filter(s=>s.kind==='stairs'||s.kind==='elevator').filter((s,i,a)=>i===0||s.kind!==a[i-1].kind).map(s=>`${s.kind==='stairs'?'Take the stairs':'Take the elevator'} ${s.toFloor>s.floor?'up':'down'}.`),`Proceed to ${name(destination.label)}.`].join('\n');
}
