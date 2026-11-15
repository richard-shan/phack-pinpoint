import {escapeHTML as e} from './live-utils.js';

// Geometry is in Matterport SDK world coordinates, never a screen-space overlay.
export function routeComponentFactory(){
  return {
    inputs:{route:null,floor:'all'},
    onInit(){this.outputs.objectRoot=new this.context.three.Group();this.onInputsUpdated();},
    onInputsUpdated(){
      const root=this.outputs.objectRoot;if(!root)return;
      root.traverse(o=>{o.geometry?.dispose();if(o.material)for(const m of [o.material].flat())m.dispose();});root.clear();
      const route=this.inputs.route;if(route?.status!=='ready')return;
      const T=this.context.three,color=route.profile==='stretcher'?0x294960:0xc72535;
      const material=()=>new T.MeshBasicMaterial({color,side:T.DoubleSide,depthTest:true});
      const point=p=>new T.Vector3(p.x,p.y+0.08,p.z);
      for(const s of route.segments){
        if(this.inputs.floor!=='all' && s.floor!==Number(this.inputs.floor) && s.toFloor!==Number(this.inputs.floor))continue;
        for(let i=1;i<s.points.length;i++){
          const a=point(s.points[i-1]),b=point(s.points[i]);if(a.distanceTo(b)<0.01)continue;
          const line=new T.LineCurve3(a,b);
          if(s.kind==='elevator'){
            const length=a.distanceTo(b);for(let d=0;d<length;d+=0.5){const aa=line.getPoint(d/length),bb=line.getPoint(Math.min(d+0.3,length)/length);root.add(new T.Mesh(new T.TubeGeometry(new T.LineCurve3(aa,bb),1,0.065,6,false),material()));}
          }else root.add(new T.Mesh(new T.TubeGeometry(line,1,0.065,6,false),material()));
          if(s.kind==='walk'||s.kind==='entry'){
            const shape=new T.Shape();shape.moveTo(0,-0.3);shape.lineTo(0.22,0.22);shape.lineTo(0,0.1);shape.lineTo(-0.22,0.22);shape.closePath();
            const arrow=new T.Mesh(new T.ShapeGeometry(shape),material());arrow.rotation.x=-Math.PI/2;arrow.rotation.z=Math.atan2(b.x-a.x,b.z-a.z);arrow.position.copy(line.getPoint(0.65));arrow.position.y+=0.01;root.add(arrow);
          }
        }
      }
      for(const [p,finish] of [[route.segments[0].points[0],false],[route.segments.at(-1).points.at(-1),true]]){
        const ring=new T.Mesh(new T.RingGeometry(finish?0.23:0.18,finish?0.36:0.29,32),new T.MeshBasicMaterial({color:finish?0xc72535:0x294960,side:T.DoubleSide}));ring.rotation.x=-Math.PI/2;ring.position.copy(point(p));root.add(ring);
      }
    },
    onDestroy(){this.inputs.route=null;this.onInputsUpdated();},
  };
}

const bbox={west:-74.65655,south:40.34845,east:-74.65445,north:40.34972};
const mercator=({lat,lng})=>[lng*20037508.342789244/180,Math.log(Math.tan((90+lat)*Math.PI/360))*20037508.342789244/Math.PI];
const [aerialWest,aerialSouth]=mercator({lng:bbox.west,lat:bbox.south});
const [aerialEast,aerialNorth]=mercator({lng:bbox.east,lat:bbox.north});
const aerialHeight=1000*(aerialNorth-aerialSouth)/(aerialEast-aerialWest);
export function aerialPoint(p,bounds=bbox){const [w,s]=mercator({lng:bounds.west,lat:bounds.south}),[east,n]=mercator({lng:bounds.east,lat:bounds.north});const height=1000*(n-s)/(east-w);return [(p.lng-bounds.west)/(bounds.east-bounds.west)*1000,(bounds.north-p.lat)/(bounds.north-bounds.south)*height];}
export function spatialShell(){return `<div id="spatial-slot"></div>`;}
export class SpatialView {
  constructor({onUpdate,property}){this.property=property;this.bounds=bbox;this.aerialHeight=aerialHeight;this.onUpdate=onUpdate;this.subscriptions=[];this.tourToken=0;this.route=null;this.incidentId=null;this.disposed=false;}
  async mount(slot,incident){
    this.incidentId=incident.id;
    this.el=document.createElement('section');this.el.className='spatial-panel';this.el.setAttribute('aria-label',`${this.property?.name||'Building'} route`);slot.replaceWith(this.el);
    this.el.innerHTML=`<div class="section-heading"><h2>Route</h2><span class="spatial-revision" aria-live="polite"></span></div><div class="spatial-toolbar"><div class="spatial-tabs" role="group" aria-label="Route view"><button data-spatial="model" aria-pressed="true">3D model</button><button data-spatial="interior" aria-pressed="false">Interior</button><button data-spatial="site" aria-pressed="false">Exterior</button></div><div class="spatial-profile" role="group" aria-label="Route profile"><button data-spatial="foot" aria-pressed="true">On foot</button><button data-spatial="stretcher" aria-pressed="false">Stretcher</button></div><div class="spatial-profile spatial-elevators" role="group" aria-label="Elevators"><button data-spatial="no-elevators" aria-pressed="true">No elevators</button><button data-spatial="elevators" aria-pressed="false">Elevators OK</button></div></div><div class="spatial-fields"><label>Entry<select name="entry" aria-label="External entry"></select></label><label>Destination<select name="destination" aria-label="Route destination"></select></label></div><div class="spatial-viewport" data-view="model"><div class="spatial-interior"><iframe title="${e(this.property?.name||'Building')} movable Matterport scan" allow="fullscreen; xr-spatial-tracking" allowfullscreen></iframe></div><div class="spatial-model"><iframe title="3D model of the building with the route" hidden></iframe><p class="spatial-model-wait" role="status">3D model · loading…</p></div><div class="spatial-site" hidden><svg viewBox="0 0 1000 ${aerialHeight}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Vexcel aerial imagery with illustrative exterior approach"><g class="aerial-tiles"></g><g class="aerial-route"></g></svg><div class="aerial-key"><strong>${e(this.property?.name||'Building')}</strong><span>North ↑</span><span class="aerial-availability" role="status">Loading imagery…</span></div><a class="aerial-credit" href="https://beta-app.matterport.com/2026-princeton-hackathon/quickstart" target="_blank" rel="noreferrer">Imagery © Vexcel</a></div></div><div class="spatial-view-controls"><button data-spatial="overview" disabled>3D overview</button><button data-spatial="entrance" disabled>Go to entry</button><button data-spatial="follow" disabled>Follow route</button><label>Show level<select name="floor" aria-label="Visible level"><option value="all">All levels</option></select></label></div><p class="spatial-status" role="status">Loading…</p><div class="spatial-route-summary"><strong class="spatial-distance"></strong><p class="spatial-reason"></p></div><div class="spatial-steps"></div><details class="spatial-editor"><summary>Edit route</summary><div class="spatial-fields"><label>From scan<select name="connector-a"></select></label><label>To scan<select name="connector-b"></select></label><label>Connection<select name="connector-kind"><option value="elevator">Elevator</option><option value="stairs">Stairs</option></select></label></div><label class="spatial-confirm"><input type="checkbox" name="confirmed">Landings confirmed</label><button data-spatial="connector">Add connection</button><button data-spatial="clear-connectors">Clear connections</button><div class="spatial-blocks"></div><button data-spatial="unblock">Clear blocked segments</button></details>`;
    this.mapResize=new ResizeObserver(()=>this.sizeMapLabels());
    this.mapResize.observe(this.el.querySelector('.spatial-viewport'));
    this.showModel(incident);                 // the 3D model does not wait for the Matterport walk-through
    this.el.addEventListener('click',event=>{const b=event.target.closest('[data-spatial]');if(b)this.perform(b.dataset.spatial,b).catch(error=>this.status(error?.message||String(error)));});
    this.el.addEventListener('change',event=>{const n=event.target.name;if(n==='entry'||n==='destination')this.request({[n==='entry'?'entryId':'destination']:event.target.value}).catch(error=>this.status(error?.message||String(error)));if(n==='floor')this.floor(event.target.value).catch(error=>this.status(error?.message||String(error)));});
    try {
      const response=await fetch('/api/spatial/config?propertyId='+encodeURIComponent(this.property?.id||'princeton-rabinowitz'));if(!response.ok)throw new Error('The building scan configuration could not load. Refresh to retry.');
      const config=await response.json();if(this.disposed)return;this.graph=config.model;
      this.el.querySelector('[data-spatial=overview]').hidden=this.graph.supportsOverview===false;
      this.bounds=this.graph.aerial?.bounds||bbox;
      const [west,south]=mercator({lng:this.bounds.west,lat:this.bounds.south}),[east,north]=mercator({lng:this.bounds.east,lat:this.bounds.north});
      this.aerialHeight=1000*(north-south)/(east-west);
      this.el.querySelector('.spatial-site svg').setAttribute('viewBox',`0 0 1000 ${this.aerialHeight}`);
      const nodes=this.graph.nodes;
      this.el.querySelector('[name=entry]').innerHTML=this.graph.entries.map(v=>`<option value="${e(v.id)}">${e(v.label)}</option>`).join('');
      for(const name of ['destination','connector-a','connector-b'])this.el.querySelector(`[name=${name}]`).innerHTML=nodes.map(n=>`<option value="${e(n.id)}">${e(n.label)}</option>`).join('');
      this.el.querySelector('[name=floor]').innerHTML='<option value="all">All levels</option>'+this.graph.floors.filter(f=>nodes.some(n=>n.floor===f.sequence)).map(f=>`<option value="${f.sequence}">${e(f.label)}</option>`).join('');
      const aerial=this.el.querySelector('.aerial-tiles');
      aerial.innerHTML=`<image width="1000" height="${this.aerialHeight}" preserveAspectRatio="none"/>`;
      const aerialImage=aerial.querySelector('image'), availability=this.el.querySelector('.aerial-availability');
      aerialImage.addEventListener('load',()=>availability.textContent='Aerial imagery');
      aerialImage.addEventListener('error',()=>availability.textContent='Vexcel imagery could not load. Check the server key and refresh.');
      aerialImage.setAttribute('href',this.graph.aerial?.url||'/api/aerial/princeton.jpg');
      this.update(incident);
      const iframe=this.el.querySelector('iframe');
      if(!config.sdkKey){iframe.src=`https://my.matterport.com/show?m=${this.graph.modelId}&play=1`;this.status('3D view unavailable.');return;}
      iframe.addEventListener('load',()=>this.connect(iframe).catch(error=>this.status(`3D overlay could not connect: ${error.message}. Open the original tour or reload to retry.`)),{once:true});
      iframe.src=`${config.bundlePath}?m=${this.graph.modelId}&applicationKey=${encodeURIComponent(config.sdkKey)}&play=1&title=0&pinpoint=3`;
      this.connectTimer=setTimeout(()=>{if(!this.sdk)this.status('The 3D scan is still loading. Check the network and that this hostname is enabled for the SDK key.');},25000);
    }catch(error){this.status(error.message);}
  }
  status(message){if(!this.disposed)this.el.querySelector('.spatial-status').textContent=message;}
  async connect(iframe){
    const sdk=await iframe.contentWindow.MP_SDK.connect(iframe.contentWindow);if(this.disposed)return;
    this.sdk=sdk;clearTimeout(this.connectTimer);
    await sdk.Scene.register('pinpoint.route',routeComponentFactory);
    const [object]=await sdk.Scene.createObjects(1);if(this.disposed){object.stop();return;}
    this.scene=object;this.component=object.addNode().addComponent('pinpoint.route',{route:this.route,floor:'all'});object.start();
    this.subscriptions.push(sdk.Sweep.current.subscribe(s=>{this.currentSweep=s.id;if(s.id)this.el.dataset.currentSweep=s.id;}));
    // Compare every route vertex against SDK pucks: fail closed if the model coordinate convention changes.
    this.subscriptions.push(sdk.Sweep.data.subscribe({onCollectionUpdated:collection=>{
      const sweeps=new Map(Object.values(collection).map(s=>[s.id,s]));
      const errors=this.graph.nodes.flatMap(n=>{const p=sweeps.get(n.id)?.puckPosition;return p?[Math.hypot(p.x-n.position.x,p.y-n.position.y,p.z-n.position.z)]:[];});
      if(errors.length){this.el.dataset.alignedPucks=String(errors.length);this.el.dataset.maxAlignmentError=String(Math.max(...errors));}
      if(errors.some(error=>error>0.3)){this.component.inputs.route=null;this.status('Scan alignment changed. Route overlay is hidden until coordinates are recalibrated.');this.alignmentError=true;}

    }}));
    this.subscriptions.push(sdk.Floor.current.subscribe(f=>{
      if(f.sequence===undefined)return;
      const value=f.sequence===-1?'all':String(f.sequence);
      this.component.inputs.floor=value;
      this.el.querySelector('[name=floor]').value=value;
    }));
    this.subscriptions.push(sdk.App.state.subscribe(s=>{if(s.phase===sdk.App.Phase.ERROR)this.status('Matterport could not load this scan. Open the original tour or refresh to retry.');if(s.phase===sdk.App.Phase.PLAYING){this.ready=true;this.el.querySelectorAll('.spatial-view-controls button').forEach(b=>b.disabled=false);if(!this.alignmentError)this.status('Route registered in the scan. Drag to look around; use the floor controls to explore.');if(!this.initialPositioned){this.initialPositioned=true;this.moveTo(this.route?.entry.nodeId||this.graph.entries[0].nodeId).catch(error=>this.status(error?.message||String(error)));}}}));
    this.update(this.latestIncident);
  }
  update(incident){
    if(this.disposed)return;this.latestIncident=incident;this.showModel(incident);
    const next=incident.spatialRoute;const changed=JSON.stringify(next)!==JSON.stringify(this.route);
    this.route=next||null;if(!this.graph)return;
    if(changed){this.tourToken++;this.following=false;this.el.querySelector('[data-spatial=follow]').textContent='Follow route';}
    if(this.component&&!this.alignmentError&&changed)this.component.inputs.route=this.route;
    this.el.querySelector('.spatial-revision').textContent=next?(next.updatedBy==='agent'?'Auto-planned':'Edited by dispatcher'):'Planning…';
    this.el.dataset.routeRevision=String(next?.revision||0);
    for(const p of ['foot','stretcher'])this.el.querySelector(`[data-spatial=${p}]`).setAttribute('aria-pressed',String((next?.profile||'foot')===p));
    const avoid=next?.avoidElevators!==false;this.el.querySelector('[data-spatial=no-elevators]').setAttribute('aria-pressed',String(avoid));this.el.querySelector('[data-spatial=elevators]').setAttribute('aria-pressed',String(!avoid));
    this.el.querySelector('[name=entry]').value=next?.entryId||this.graph.entries[0].id;
    this.el.querySelector('[name=destination]').value=next?.destination||this.graph.defaultDestination;
    this.el.querySelector('.spatial-distance').textContent=next?.message||'Choose a destination to plan from the external entry.';
    this.el.querySelector('.spatial-reason').textContent=next?.reason||'Route proposals require dispatcher review.';
    const entry=next?.entry||this.graph.entries[0];const pts=entry.approach.map(p=>aerialPoint(p,this.bounds));
    this.el.querySelector('.aerial-route').innerHTML=`<polyline points="${pts.map(p=>p.join(',')).join(' ')}" class="aerial-line ${next?.profile==='stretcher'?'step-free':''}"/><circle cx="${pts[0][0]}" cy="${pts[0][1]}" r="10" class="aerial-start"/><circle cx="${pts.at(-1)[0]}" cy="${pts.at(-1)[1]}" r="12" class="aerial-entry"/><text x="${pts[0][0]+20}" y="${pts[0][1]+8}">Start</text><text x="${pts.at(-1)[0]+20}" y="${pts.at(-1)[1]+8}">Entry</text>`;
    this.sizeMapLabels();
    const phases=next?.status==='ready'?[{label:entry.label,sub:'Entry',id:entry.nodeId},...next.segments.filter(s=>['stairs','elevator'].includes(s.kind)).filter((s,i,a)=>i===0||s.kind!==a[i-1].kind).map(s=>({label:s.kind==='stairs'?'Main stair':'Elevator',sub:(s.toFloor>s.floor?'Up':'Down'),id:this.graph.edges.find(edge=>edge.id===s.id)?.b||next.destination})),{label:String(this.graph.nodes.find(n=>n.id===next.destination).label).replace(/^Scan \d+ · /,''),sub:'Caller',id:next.destination}]:[];
    this.el.querySelector('.spatial-steps').innerHTML=phases.map((p,i)=>`<button data-spatial="jump" data-node="${e(p.id)}"><span>${i+1}</span><strong>${e(p.label)}</strong><small>${e(p.sub)}</small></button>`).join('');
    this.el.querySelector('.spatial-blocks').innerHTML=(next?.segments||[]).filter(s=>s.kind!=='entry').map(s=>`<button data-spatial="block" data-edge="${e(s.id)}">Block ${e(s.kind)} ${e(s.id)}</button>`).join('');
    this.el.querySelector('[data-spatial=clear-connectors]').disabled=!next?.connectors?.length;
    this.el.querySelector('[data-spatial=unblock]').disabled=!next?.blockedEdges?.length;
  }
  async request(update){
    if(this.saving)return;this.saving=true;
    this.el.setAttribute('aria-busy','true');
    const controls=[...this.el.querySelectorAll('button,select')].map(control=>[control,control.disabled]);
    const trigger=document.activeElement;
    if(trigger?.matches('button'))trigger.setAttribute('aria-busy','true');
    controls.forEach(([control])=>control.disabled=true);
    this.status('Saving route changes…');
    try{
      await this.onUpdate({...update,updatedBy:'dispatcher'});
      this.status('Route updated. Review this revision before approving the packet.');
    }finally{
      this.saving=false;this.el.removeAttribute('aria-busy');trigger?.removeAttribute('aria-busy');
      controls.forEach(([control,disabled])=>{if(control.isConnected)control.disabled=disabled;});
      if(!this.disposed)this.update(this.latestIncident);
    }
  }
  async floor(value){
    if(this.component)this.component.inputs.floor=value;
    if(this.sdk&&this.ready){if(value==='all')await this.sdk.Floor.showAll();else await this.sdk.Floor.moveTo(Number(value));}
  }
  sizeMapLabels(){
    const width=this.el?.querySelector('.spatial-site svg')?.getBoundingClientRect().width;
    if(!width)return;
    this.el.querySelectorAll('.aerial-route text').forEach(label=>label.style.fontSize=`${Math.max(20,14*1000/width)}px`);
  }
  // Views: 'model' (the 3D model, default), 'interior' (Matterport walk-through), 'site' (aerial).
  // The walk-through stays loaded under the 3D model so switching to it is instant.
  showTab(view){view=view===true?'site':view===false?'interior':view;this.el.querySelector('.spatial-viewport').dataset.view=view;this.el.querySelector('.spatial-site').hidden=view!=='site';this.el.querySelector('.spatial-interior').hidden=view==='site';this.el.querySelector('.spatial-model').hidden=view!=='model';for(const tab of ['model','interior','site'])this.el.querySelector(`[data-spatial=${tab}]`).setAttribute('aria-pressed',String(tab===view));}
  // The 3D model page draws the route the agent wrote; it exists once the agent has run.
  showModel(incident){
    const frame=this.el?.querySelector('.spatial-model iframe');if(!frame)return;
    const ready=/\/assets\/real\/live\/[\w.-]+\/route\.jpg/.test(incident.packetMarkdown||'');
    if(ready&&!frame.src){frame.src=`/live-3d.html?embed=1#${encodeURIComponent(incident.id)}`;frame.hidden=false;this.el.querySelector('.spatial-model-wait').hidden=true;}
  }
  async moveTo(id){
    if(!this.ready)throw new Error('Wait for the 3D scan to finish loading.');
    this.showTab(false);
    // Face the way the crew is walking: toward the next position on the route, or, at the
    // last one, the same way they arrived. The viewer looks along -z at yaw 0 and turns
    // toward -x as yaw grows, so the heading of a step (dx, dz) is atan2(-dx, -dz).
    const path=this.route?.nodeIds||[],index=path.indexOf(id);
    const at=nodeId=>this.graph.nodes.find(n=>n.id===nodeId)?.position;
    const [a,b]=index>=0&&path[index+1]?[at(id),at(path[index+1])]:index>0?[at(path[index-1]),at(id)]:[undefined,undefined];
    const level=a&&b&&Math.hypot(b.x-a.x,b.z-a.z)>0.3;      // a stair or elevator step has no heading
    const rotation=this.graph.faceRoute&&level?{x:-12,y:Math.atan2(-(b.x-a.x),-(b.z-a.z))*180/Math.PI}:undefined;
    await this.sdk.Sweep.moveTo(id,{...(rotation?{rotation}:{}),transition:this.sdk.Camera.TransitionType.FLY,transitionTime:650});
  }
  async perform(action,button){
    if(action==='site'||action==='interior'||action==='model'){this.showTab(action);return;}
    if(action==='foot'||action==='stretcher')return this.request({profile:action,reason:action==='stretcher'?'Stretcher route.':'On-foot route.'});
    if(action==='no-elevators'||action==='elevators')return this.request({avoidElevators:action==='no-elevators',reason:action==='no-elevators'?'Elevators excluded.':'Elevators allowed.'});
    if(action==='block')return this.request({blockedEdges:[...new Set([...(this.route?.blockedEdges||[]),button.dataset.edge])],reason:`Segment ${button.dataset.edge} blocked; route recalculated.`});
    if(action==='unblock')return this.request({blockedEdges:[],reason:'Blocked segments reopened.'});
    if(action==='clear-connectors')return this.request({connectors:[],blockedEdges:[],reason:'Dispatcher-mapped connections removed.'});
    if(action==='connector'){
      if(!this.el.querySelector('[name=confirmed]').checked)throw new Error('Confirm the landings before adding this connection.');
      return this.request({connectors:[...(this.route?.connectors||[]),{a:this.el.querySelector('[name=connector-a]').value,b:this.el.querySelector('[name=connector-b]').value,kind:this.el.querySelector('[name=connector-kind]').value,confirmed:true}],reason:'Dispatcher mapped a floor connection.'});
    }
    if(action==='overview'){if(!this.ready)return;this.tourToken++;this.showTab(false);await this.sdk.Mode.moveTo(this.sdk.Mode.Mode.DOLLHOUSE);await this.floor('all');return;}
    if(action==='jump'||action==='entrance'){this.tourToken++;return this.moveTo(action==='jump'?button.dataset.node:this.route?.entry.nodeId||this.graph.entries[0].nodeId);}
    if(action==='follow'){
      if(this.following){this.tourToken++;this.following=false;button.textContent='Follow route';return;}
      if(this.route?.status!=='ready')throw new Error('There is no connected route to follow.');
      const token=++this.tourToken,path=[...this.route.nodeIds];this.following=true;button.textContent='Stop following';
      try{for(const [index,id] of path.entries()){if(token!==this.tourToken||this.disposed)break;this.el.dataset.tourProgress=String(index+1);this.status(`Following route · ${index+1} of ${path.length} scan positions`);if(id!==this.currentSweep)await this.moveTo(id);}if(token===this.tourToken)this.status('Route walkthrough complete.');}
      finally{this.following=false;if(!this.disposed)button.textContent='Follow route';}
    }
  }
  dispose(){this.mapResize?.disconnect();this.disposed=true;this.tourToken++;clearTimeout(this.connectTimer);this.subscriptions.forEach(s=>s.cancel?.());this.scene?.stop();}
}
