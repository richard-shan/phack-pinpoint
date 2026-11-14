import {escapeHTML as e, safeLink} from './live-utils.js';
export const LA_DEMO_ID='5956257';
export function aerialShell(property){
  const model=property.models?.find(m=>m.joinStatus==='verified');
  if(!['Los Angeles','Houston'].includes(property.city))return '';
  return `<section class="property-aerial" aria-label="Aerial and interior context"><div class="section-heading"><h2>Outside & inside</h2><span class="muted">Vexcel · Matterport</span></div><div class="spatial-toolbar"><div class="spatial-tabs" role="group" aria-label="Building view"><button data-building-view="aerial" aria-pressed="true">Aerial imagery</button>${model?'<button data-building-view="interior" aria-pressed="false">3D interior</button>':''}</div></div><div class="property-aerial-frame"><div class="property-aerial-image"><img hidden alt="Vexcel aerial imagery centered on ${e(property.address)}"><p class="property-aerial-status" role="status">Loading Vexcel aerial imagery…</p><button class="property-aerial-retry" hidden>Retry imagery</button></div>${model?`<iframe hidden title="Matterport interior at ${e(property.address)}" data-tour="${e(safeLink(model.url))}" allow="fullscreen; xr-spatial-tracking" allowfullscreen></iframe>`:''}</div><p class="photo-caption">Vexcel aerial imagery · North up · 140 m × 140 m centered on the CoStar property coordinates. Capture time is not confirmed; imagery is not live.</p><p class="hint">Review entrances and staging with the caller. No verified exterior or interior route has been mapped for this property.</p></section>`;
}
export function mountAerial(el,property){
  if(!el)return null;
  const controller=new AbortController();let objectUrl;
  const img=el.querySelector('img'),status=el.querySelector('[role=status]'),retry=el.querySelector('.property-aerial-retry');
  async function load(){
    retry.hidden=true;status.hidden=false;status.textContent='Loading Vexcel aerial imagery…';
    try{
      const response=await fetch(`/api/aerial/${encodeURIComponent(property.id)}.jpg`,{signal:controller.signal});
      if(!response.ok){const data=await response.json();throw new Error(data.error?.message||'Aerial imagery could not load.');}
      const blob=await response.blob();if(controller.signal.aborted)return;
      if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=URL.createObjectURL(blob);
      img.src=objectUrl;await img.decode();if(controller.signal.aborted)return;
      img.hidden=false;status.textContent='Vexcel aerial imagery loaded.';status.hidden=true;
    }catch(error){if(controller.signal.aborted)return;img.hidden=true;status.textContent=error.message;retry.hidden=false;}
  }
  retry.addEventListener('click',load,{signal:controller.signal});
  el.querySelectorAll('[data-building-view]').forEach(button=>button.addEventListener('click',()=>{
    const interior=button.dataset.buildingView==='interior';
    el.querySelector('.property-aerial-image').hidden=interior;
    const iframe=el.querySelector('iframe');if(iframe){iframe.hidden=!interior;if(interior&&!iframe.src)iframe.src=iframe.dataset.tour;}
    el.querySelectorAll('[data-building-view]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  },{signal:controller.signal}));
  load();
  return {dispose(){controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);}};
}
