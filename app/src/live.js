import {LA_DEMO_ID, aerialShell, mountAerial} from './live-aerial.js';
import {SpatialView} from './live-spatial.js';
import {PRINCETON_ID} from './live-route-model.js';
import {escapeHTML as e, known, safeImage, safeLink, renderMarkdown, packetText, sourceLabel} from './live-utils.js';
const main = document.querySelector('#main'), dialog = document.querySelector('#dialog');
let spatialView, aerialView;
let catalog, recent = [], selected, incident, property, snapshot, chosenImage = 0, routeVersion = 0, busy = false, toastTimer, polling;
const imageList = p => (p.models || []).filter(m => m.joinStatus === 'verified').flatMap(m => (m.images || []).map(i => ({...i,model:m})));
const isSpatial = p => !!p && (p.id === PRINCETON_ID || p.id === LA_DEMO_ID || p.spatial === true);
const statusText = s => ({review:'Awaiting review',approved:'Approved locally',rejected:'Changes requested'}[s] || s);
const time = value => value ? new Date(value).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '';
const pretty = value => value == null ? 'Not provided' : Number(value).toLocaleString();
function toast(text) { clearTimeout(toastTimer); document.querySelector('#feedback').textContent = text; toastTimer = setTimeout(() => document.querySelector('#feedback').textContent = '', 7000); }
async function api(path, method='GET', data) {
  const response = await fetch('/api'+path,{method,headers:data ? {'Content-Type':'application/json'} : {},body:data ? JSON.stringify(data) : undefined});
  let result; try {result=await response.json();} catch {throw new Error('The local service returned an unreadable response. Restart npm run dev:real.');}
  if(!response.ok) { const error=new Error(result.error?.message || 'The request failed.');error.status=response.status;throw error; } return result;
}
async function writeAPI(path, method, data) {
  const version=routeVersion;
  const result=await api(path,method,data);
  if(version!==routeVersion) throw new Error('The previous incident was saved. Reopen it to see the update.');
  return result;
}
const loadingView = (label = 'Loading workspace…') => `<div class="loading-state" role="status"><p>${e(label)}</p><div class="skeleton-grid" aria-hidden="true"><div class="skeleton-lines"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div><div class="skeleton"></div></div></div>`;
async function action(fn, trigger = document.activeElement) {
  if(busy) return;
  busy = true;
  const controls = [...document.querySelectorAll('button')].map(button => [button, button.disabled]);
  const button = trigger?.matches('button') ? trigger : null;
  if(button) button.setAttribute('aria-busy', 'true');
  controls.forEach(([control]) => control.disabled = true);
  try { await fn(); } catch(error) {
    const message = error.status === 409 ? 'This incident changed in another window. Your unsaved text is still here; close this form and refresh the incident before trying again.' : error.message;
    const target = dialog.open ? dialog.querySelector('.form-error') : null;
    if(target) target.textContent = message;
    toast(message);
  } finally {
    busy = false;
    // Restore only controls we disabled. Newly rendered and SDK-owned controls keep their state.
    controls.forEach(([control, wasDisabled]) => { if(control.isConnected) control.disabled = wasDisabled; });
    if(button?.isConnected) button.removeAttribute('aria-busy');
  }
}
function openDialog(title,body) {dialog.innerHTML=`<div class="dialog-head"><h2 id="dialog-title">${e(title)}</h2><button type="button" data-action="close">Close</button></div>${body}<p class="form-error" role="alert"></p>`;dialog.showModal();}
function facts(p) {return `<dl class="facts"><div><dt>Use</dt><dd>${e(known(p.useType))}</dd></div><div><dt>Stories</dt><dd>${e(known(p.stories))}</dd></div><div><dt>Year built</dt><dd>${e(known(p.yearBuilt))}</dd></div><div><dt>Area · sq ft</dt><dd>${pretty(p.sqft)}</dd></div></dl>`;}
function sourceLink(p) {return `<button class="small" data-action="source" data-property="${e(p.id)}">${p.id===PRINCETON_ID?'Inspect scan sources':'Inspect CoStar record'}</button>`;}
function home() {
  incident=property=snapshot=null;
  main.innerHTML=`<div class="intro"><div><h1>Dispatch workspace</h1><p>Select a building, review the caller’s evidence, and prepare a responder packet.</p></div><a class="button" href="#los-angeles">Explore Los Angeles</a></div><p class="source-note">Building records · Matterport references · Local review only</p><div class="catalog-layout"><section class="catalog-search" aria-labelledby="search-heading"><h2 id="search-heading">Find a building</h2><div class="search-row"><div><label for="search">Name, address, or property ID</label><input id="search" type="search" placeholder="Try Geneva or 3160 Geneva"></div><div><label for="city">City</label><select id="city"><option value="">All cities</option>${[...new Set(catalog.properties.map(p=>p.city).filter(Boolean))].sort().map(city=>`<option>${e(city)}</option>`).join('')}</select></div></div><p class="list-count" id="list-count" role="status" aria-live="polite"></p><div class="property-list" id="property-list"></div></section><section class="property-detail" id="property-detail" aria-label="Selected building" tabindex="-1"></section></div><section class="recent"><h2>Recent incidents</h2><p class="muted">Saved on this local server. Open the caller view in a second browser to test the conversation.</p><div class="recent-list">${recent.length ? recent.map(i=>`<a class="recent-row" href="#incident/${e(i.id)}"><strong>${e(catalog.properties.find(p=>p.id===i.propertyId)?.name || i.propertyId)}</strong><span>${e(statusText(i.status))} · revision ${i.revision} · ${e(time(i.updatedAt))}</span></a>`).join('') : '<p class="empty-state">No incidents yet. Select a building to start.</p>'}</div></section>`;
  renderList(); renderProperty();
}
function renderList() {
  const q=document.querySelector('#search').value.toLowerCase().trim(), city=document.querySelector('#city').value;
  const matches=catalog.properties.filter(p=>(!city || p.city===city)&&`${p.id} ${p.name} ${p.address} ${p.city}`.toLowerCase().includes(q));
  document.querySelector('#list-count').textContent=`${matches.length.toLocaleString()} properties${matches.length>60 ? ' · Showing the first 60; search to narrow results' : ''}`;
  document.querySelector('#property-list').innerHTML=matches.length ? matches.slice(0,60).map(p=>`<button class="property-row" data-action="select" data-property="${e(p.id)}" aria-pressed="${p.id===selected.id}"><strong>${e(p.name || p.address)}</strong><span>${e(p.address)}</span><span>${e(sourceLabel(p))}${imageList(p).length ? ' · Offline interior images' : ''}</span></button>`).join('') : '<p class="empty-state">No matching buildings. Try another address or clear your filters.</p><button data-action="clear-search">Clear filters</button>';
}
function renderProperty() {
  const p=selected,images=imageList(p);
  document.querySelector('#property-detail').innerHTML=`<h2>${e(p.name || p.address)}</h2><p class="muted">${e(p.address)}</p>${images.length ? `<img class="lead-photo" src="${e(safeImage(images[0].url))}" alt="Matterport interior reference for ${e(p.name)}"><p class="photo-caption">Matterport reference · ${e(images[0].model.name)}. Source join checked by property ID and street address; caller location is not inferred.</p>` : `<div class="empty-photo">${p.id===PRINCETON_ID?'Explore this building in the Princeton 3D view.<br>The Matterport scan and route sources are available for review.':'No offline interior image for this property.<br>The CoStar record is available for review.'}</div>`}${facts(p)}<div class="actions"><button class="primary" data-action="new">Start incident</button>${sourceLink(p)}</div><p class="hint">${e(sourceLabel(p))}. Current occupancy, access, and equipment must be confirmed.</p>`;
}
function messageHTML(i) {
  return (i.messages || []).length ? i.messages.map(m=>`<article class="message ${m.role==='dispatcher'?'dispatcher':''}"><small><strong>${m.role==='dispatcher'?'Dispatcher':'Caller'}</strong><time>${e(time(m.at))}</time></small><p>${e(m.text)}</p></article>`).join('') : '<p class="empty-state">No messages yet.</p>';
}
function composer(role) {return `<form id="message-form" class="composer" data-role="${role}"><label for="message">${role==='caller'?'Reply to dispatcher':'Message caller'}</label><textarea id="message" name="message" required maxlength="4000" placeholder="${role==='caller'?'Describe what you can see…':'Ask a focused question…'}"></textarea><button class="primary" type="submit">Send ${role==='caller'?'reply':'message'} locally</button></form>`;}
function renderCase() {
  aerialView?.dispose();aerialView=null;
  if(isSpatial(property)){renderSpatialCase();return;}
  const i=incident,p=property,images=imageList(p),img=images[chosenImage]||images[0];
  main.innerHTML=`<div class="destination"><div><h1>${e(p.name)}</h1><p>${e(p.address)}</p></div><span class="status status-${e(i.status)}">${e(statusText(i.status))} · Revision ${i.revision}</span></div><div class="case-toolbar"><a href="#">All incidents</a><p class="muted">Real records · Manual review · Optional messaging bridge</p><div class="actions"><button class="small" data-action="scroll-review">Review packet</button><button class="small" data-action="scroll-chat">Caller conversation</button><button class="small" data-action="refresh">Refresh incident</button></div></div><div class="workspace"><div>${aerialShell(p)}<section><div class="section-heading"><h2>Compare the evidence</h2><span class="muted">Location not automatically matched</span></div><div class="evidence-pair"><figure><h3>Caller photo</h3>${i.photo?.dataUrl ? `<img src="${e(safeImage(i.photo.dataUrl))}" alt="Photo submitted for this incident">` : '<div class="empty-image">No caller photo received.<br>Use the caller view to attach one.</div>'}<figcaption>${i.photo ? e(i.photo.name) : 'Caller-provided image; no automated analysis.'}</figcaption></figure><figure><h3>Building reference</h3>${img ? `<a href="${e(safeImage(img.url))}" target="_blank" aria-label="Open full source image"><img id="reference-image" src="${e(safeImage(img.url))}" alt="Matterport source interior ${e(img.model.name)}"></a><figcaption>${e(img.model.name)} · ${e(img.caption)}<br>Scan date not confirmed. <a href="${e(safeLink(img.model.url))}" target="_blank" rel="noreferrer">Open source model</a></figcaption>` : '<div class="empty-image">No cached panorama available for this building.</div>'}</figure></div>${images.length>1 ? `<div class="thumbs" aria-label="Building reference images">${images.slice(0,24).map((im,n)=>`<button data-action="image" data-index="${n}" aria-label="Reference image ${n+1}" aria-pressed="${n===chosenImage}"><img src="${e(safeImage(im.url))}" alt=""></button>`).join('')}</div>`:''}</section><section><div class="section-heading"><h2>Location & access</h2><button class="small" data-action="edit">Edit review</button></div><div class="route-summary"><div><h3>Caller location</h3><p>${e(i.location || 'Not confirmed. Ask the caller for floor, suite, and nearby landmarks.')}</p></div><div><h3>Entrance, staging & route</h3><p>${e(i.route || 'Not confirmed. No verified route geometry or aerial analysis has been supplied.')}</p></div></div><p class="hint">Entered directions are dispatcher-provided. No route calculation or accessibility check runs here.</p><div class="unconfirmed"><h3>Not confirmed</h3><ul>${(i.unconfirmed||[]).map(x=>`<li>${e(x)}</li>`).join('') || '<li>No additional unknowns entered. Source and route limitations still apply.</li>'}</ul></div></section><section><div class="section-heading"><h2>Building brief</h2>${sourceLink(p)}</div>${facts(p)}<p class="muted">CoStar record ${e(p.id)} · ${e(p.subtype || p.useType)}. Tenant identity and on-site equipment are not established by this record.</p></section><section id="review" class="review-section"><div class="section-heading"><h2>Review packet</h2><strong class="status-${e(i.status)}">${e(statusText(i.status))}</strong></div><p>${e(i.notes || 'No dispatcher notes yet.')}</p>${i.decision?.kind==='rejected'?`<p class="form-error">Reason: ${e(i.decision.reason)}</p>`:''}${i.packetMarkdown?`<details><summary>Imported team packet</summary><div class="markdown">${renderMarkdown(i.packetMarkdown)}</div></details>`:'<p class="muted">Import a team packet when available, or review the building brief and your own notes now.</p>'}<div class="actions"><button data-action="preview">Preview packet</button><button data-action="edit">Edit</button><button data-action="import">Import packet.md</button>${i.status==='review'?'<button data-action="reject">Reject</button><button class="primary" data-action="approve">Approve packet</button>':''}</div>${i.status==='approved'&&i.packetId ? `<div class="snapshot-note">Revision ${i.revision} is saved as an immutable local packet. Later edits require a new approval.<div class="actions"><a class="button primary" href="#packet/${e(i.packetId)}">Open approved packet</a><button data-action="copy-packet">Copy packet link</button></div></div>`:''}<p class="hint">Approval saves a snapshot. A connected messaging bridge sends it to the incident’s caller. This demo does not dispatch emergency help.</p></section></div><aside class="chat-column" id="conversation" tabindex="-1"><h2>Caller conversation</h2><p class="muted">Working local channel · updates automatically</p><div class="chat-log" id="chat-log">${messageHTML(i)}</div>${composer('dispatcher')}<a class="button" href="#caller/${e(i.id)}" target="_blank">Open caller view</a><button class="small" data-action="copy-caller">Copy caller link</button><details class="trace"><summary>Incident activity · ${(i.events||[]).length} events</summary><p class="hint">Actual local actions. The team agent is not connected.</p><ol>${(i.events||[]).map(v=>`<li>${e(v.text)}<time>${e(time(v.at))}</time></li>`).join('')}</ol></details></aside></div>`;
  aerialView=mountAerial(main.querySelector('.property-aerial'),p);
}
function renderSpatialCase() {
  if(location.hostname==='127.0.0.1'){const url=new URL(location.href);url.hostname='localhost';location.replace(url);return;}
  const i=incident,p=property;
  const existing=spatialView?.incidentId===i.id && spatialView.el?.isConnected;
  if(!existing){
    spatialView?.dispose();
    main.innerHTML=`<div class="destination"><div><h1>${e(p.name)}</h1><p>${e(p.address)}</p></div><span class="status" id="princeton-status"></span></div><div class="case-toolbar"><a href="#">All incidents</a><p class="muted">${p.spatial?'Real scan · Routes from the building graph · Local review':'Real scan · Demo routing · Local review'}</p><div class="actions"><button class="small" data-action="scroll-review">Review packet</button><button class="small" data-action="scroll-chat">Caller conversation</button></div></div><div class="workspace"><div><div id="spatial-slot"></div><section id="review" class="review-section"></section><details class="trace"><summary>Building & source details</summary><p>${e(p.name)} · Model ${e(p.models?.[0]?.id||'')}. ${p.spatial?'Route geometry comes from the building graph built from this scan; doors and elevators were marked by hand.':'Route geometry is an illustrative interpretation of the captured scan.'}</p><p>Matterport floor coordinates · Vexcel aerial imagery. Exterior anchors and current access require verification.</p>${sourceLink(p)}</details></div><aside class="chat-column" id="conversation" tabindex="-1"><h2>Caller conversation</h2><p class="muted">Local messages · Live route revisions</p><div id="chat-log" class="chat-log"></div>${composer('dispatcher')}<a class="button" href="#caller/${e(i.id)}">Open caller view</a><button class="button" data-action="copy-caller">Copy caller link</button><details class="trace"><summary>Agent & route activity</summary><ol id="princeton-events"></ol></details></aside></div>`;
    spatialView=new SpatialView({property:p,onUpdate:async data=>{
      const id=incident.id,revision=incident.revision;
      const next=await api(`/incidents/${id}/route`,'POST',{expectedRevision:revision,...data});
      if(incident?.id!==id)return;
      incident=next;renderSpatialCase();
    }});
    spatialView.mount(document.querySelector('#spatial-slot'),i);
  } else spatialView.update(i);
  document.querySelector('#princeton-status').className=`status status-${i.status}`;
  document.querySelector('#princeton-status').textContent=`${statusText(i.status)} · Revision ${i.revision}`;
  const chat=document.querySelector('#chat-log'),scroll=chat.scrollTop;
  chat.innerHTML=messageHTML(i);chat.scrollTop=scroll;
  document.querySelector('#princeton-events').innerHTML=i.events.slice(-12).reverse().map(ev=>`<li><time>${e(time(ev.at))}</time>${e(ev.text)}</li>`).join('');
  document.querySelector('#review').innerHTML=`<div class="section-heading"><h2>Review packet</h2><strong class="status-${e(i.status)}">${e(statusText(i.status))}</strong></div><div class="route-summary"><div><h3>Destination</h3><p>${e(i.location||'Not selected')}</p></div><div><h3>Entry & route</h3><p>${e(i.route||'No route planned')}</p></div></div><p>${e(i.notes||'Inspect the approach and route before approving this revision.')}</p><details open class="unconfirmed"><summary>Not confirmed · ${i.unconfirmed.length}</summary><ul>${i.unconfirmed.map(v=>`<li>${e(v)}</li>`).join('')}</ul></details>${i.packetMarkdown?`<details class="trace"><summary>Agent packet</summary><div class="markdown">${renderMarkdown(i.packetMarkdown)}</div></details>`:''}${i.photo?.dataUrl?`<details class="trace"><summary>Caller photo</summary><img class="attachment-preview" src="${e(safeImage(i.photo.dataUrl))}" alt="Photo submitted for this incident"></details>`:''}${(m=>m?`<details open class="trace"><summary>Route on the floor plan</summary><a href="${e(safeImage(m[0]))}" target="_blank" aria-label="Open the route picture"><img class="match-evidence" src="${e(safeImage(m[0]))}" alt="Floor plan with the route from the staging spot through the chosen door to the caller"></a></details>`:'')(/\/assets\/real\/live\/[\w.-]+\/route\.jpg/.exec(i.packetMarkdown||''))}${(m=>m?`<details open class="trace"><summary>Photo match evidence</summary><img class="match-evidence" src="${e(safeImage(m[0]))}" alt="Caller photo beside the matched view of the scan, with matched points joined"><p class="hint">Caller photo (left) beside the matched view of the scan. Lines join points found in both.</p></details>`:'')(/\/assets\/real\/live\/[\w.-]+\/match\.jpg/.exec(i.packetMarkdown||''))}${i.decision?.kind==='rejected'?`<p class="form-error">${e(i.decision.reason)}</p>`:''}<div class="actions"><button data-action="preview">Preview packet</button><button data-action="edit">Edit notes</button>${i.status==='review'?'<button data-action="reject">Reject</button><button class="primary" data-action="approve">Approve packet</button>':''}${i.packetId?`<a class="button" href="#packet/${e(i.packetId)}">Open approved packet</a>`:''}</div><p class="hint">Any route change invalidates approval. Nothing is sent to emergency services.</p>`;
}
function renderCaller() {
  main.innerHTML=`<div class="caller-shell"><div class="destination"><div><h1>Caller conversation</h1><p>${e(property.name)} · ${e(property.address)}</p></div></div><p class="source-note">Local demonstration channel. Messages reach this computer’s dispatcher console only.</p><div class="chat-log" id="chat-log">${messageHTML(incident)}</div>${composer('caller')}<div class="field"><label for="caller-photo">Attach a photo</label><input id="caller-photo" type="file" accept="image/png,image/jpeg,image/webp"><p class="hint">PNG, JPEG, or WebP · up to 2 MB. Uploading does not locate you automatically.</p>${incident.photo?.dataUrl ? `<img class="attachment-preview" src="${e(safeImage(incident.photo.dataUrl))}" alt="Current caller attachment">`:''}</div><p class="muted">Messages are saved locally and refresh automatically. Revision ${incident.revision}.</p><div class="actions"><a href="#incident/${e(incident.id)}">Open dispatcher console</a><button data-action="refresh">Refresh conversation</button></div></div>`;
}
function packetBody(i,p) {
  const img=imageList(p)[0];
  return `<div class="destination"><div><h1>Responder review packet</h1><p>${e(p.name)}<br>${e(p.address)}</p></div><span class="status status-${e(i.status)}">${e(statusText(i.status))} · r${i.revision}</span></div><div class="packet-body"><p class="source-note">Local demonstration. No emergency service or external delivery connected. Building records do not confirm current conditions.</p><div class="packet-grid"><section><h2>Caller location</h2><p>${e(i.location || 'Not confirmed.')}</p></section><section><h2>Entrance, staging & route</h2><p>${e(i.route || 'Not confirmed. No route supplied.')}</p><p class="hint">${i.spatialRoute ? 'Computed demo route · scan coordinates · revision '+i.spatialRoute.revision : 'Dispatcher-entered directions, if present; not a computed route.'}</p></section></div><section><h2>Building brief</h2>${facts(p)}<p class="muted">${e(sourceLabel(p))} · ${e(p.subtype || p.useType)} · Tenant and equipment not confirmed.</p></section>${i.photo||img?`<div class="packet-grid"><section><h2>Caller evidence</h2>${i.photo?.dataUrl?`<img class="photo" src="${e(safeImage(i.photo.dataUrl))}" alt="Caller attachment">`:'<p>No caller photo.</p>'}</section><section><h2>Source reference</h2>${img?`<img class="photo" src="${e(safeImage(img.url))}" alt="Matterport source reference"><p class="photo-caption">${e(img.model.name)} · address-checked reference, not a caller match.</p>`:'<p>No cached reference.</p>'}</section></div>`:''}<section><h2>Dispatcher notes</h2><p>${e(i.notes || 'None recorded.')}</p></section><section class="unconfirmed"><h2>Not confirmed</h2><ul>${(i.unconfirmed||[]).map(x=>`<li>${e(x)}</li>`).join('')}</ul></section>${i.packetMarkdown?`<section><h2>Imported packet</h2><p class="muted">Imported text requires source verification. Its claims are not independently checked by this console.</p><div class="markdown">${renderMarkdown(i.packetMarkdown)}</div></section>`:''}<section><h2>Source & revision</h2><p class="muted">${e(p.source?.file || 'commercial-property.jsonl')} · record ${e(p.id)}<br>Incident ${e(i.id)} · revision ${i.revision}${i.decision?.at?` · decision ${e(new Date(i.decision.at).toLocaleString())}`:''}<br>Scan date, current access, equipment availability, and caller match confidence are not established.</p></section></div>`;
}
function renderPacket(i,p,immutable) {
  main.innerHTML=`<div class="packet-shell"><div class="actions no-print"><a class="button" href="#incident/${e(i.id)}">Back to incident</a><button data-action="print">Print / PDF</button></div><details class="packet-tools no-print"><summary>Export & audio options</summary><div class="actions"><button data-action="markdown">Export Markdown</button><button data-action="html">Export portable HTML</button>${immutable?'<button data-action="copy-packet">Copy local link</button>':''}<button data-action="speak">Read brief aloud</button><button data-action="stop-speech">Stop voice</button></div></details><p class="hint no-print">${immutable?'This approved revision is immutable and works across browsers on this computer while the server runs.':'Draft preview. Approval is required before a permanent local packet link is created.'} HTML export includes the images and can be opened offline.</p><p class="voice-status no-print" id="voice-status"></p>${packetBody(i,p)}</div>`;
}
async function navigate() {
  aerialView?.dispose();aerialView=null;
  const version=++routeVersion; clearInterval(polling); chosenImage=0; snapshot=null;
  const hash=location.hash.slice(1);
  if(spatialView && hash!==`incident/${spatialView.incidentId}` && hash!=='review'){spatialView.dispose();spatialView=null;}
  if(hash==='review'&&incident) {document.querySelector('#review')?.scrollIntoView();startPoll();return;}
  if(spatialView){spatialView.dispose();spatialView=null;}
  main.setAttribute('aria-busy','true');
  main.innerHTML=loadingView(hash ? 'Opening incident…' : 'Loading buildings…');
  window.scrollTo({top:0,behavior:'instant'});
  try {
    if(hash==='los-angeles'){
      const list=(await api('/incidents')).incidents;
      let demo=list.find(i=>i.propertyId===LA_DEMO_ID);
      if(!demo)demo=await api('/incidents','POST',{propertyId:LA_DEMO_ID,callerText:'Demo scenario: caller reports being near the lobby at 3160 Geneva Street. Confirm their exact location and access with the caller.'});
      if(!demo.spatialRoute)demo=await api(`/incidents/${demo.id}/route`,'POST',{expectedRevision:demo.revision,profile:'foot',reason:'Initial illustrative Los Angeles lobby route.'});
      if(version===routeVersion)location.hash=`incident/${demo.id}`;return;
    }
    if(hash==='princeton'){
      const list=(await api('/incidents')).incidents;
      let demo=list.find(i=>i.propertyId===PRINCETON_ID);
      if(!demo)demo=await api('/incidents','POST',{propertyId:PRINCETON_ID,callerText:'Demo scenario: caller at the upper atrium gallery. This destination is selected for demonstration, not automatically localized.'});
      if(!demo.spatialRoute)demo=await api(`/incidents/${demo.id}/route`,'POST',{expectedRevision:demo.revision,profile:'foot',updatedBy:'agent',reason:'Initial demo route from the external approach to the upper gallery.'});
      if(version===routeVersion)location.hash=`incident/${demo.id}`;return;
    }
    if(hash.startsWith('packet/')) {const loadedSnapshot=await api('/packets/'+encodeURIComponent(hash.slice(7)));if(version!==routeVersion)return;snapshot=loadedSnapshot;incident=snapshot.incident;property=snapshot.property;renderPacket(incident,property,true);return;}
    if(hash.startsWith('incident/')||hash.startsWith('caller/')||hash.startsWith('preview/')) {
      const caller=hash.startsWith('caller/'), id=hash.split('/')[1];
      let loaded=await api('/incidents/'+encodeURIComponent(id));if(version!==routeVersion)return;
      if(hash.startsWith('incident/')&&loaded.propertyId===LA_DEMO_ID&&!loaded.spatialRoute){loaded=await api(`/incidents/${loaded.id}/route`,'POST',{expectedRevision:loaded.revision,profile:'foot',reason:'Initial illustrative Los Angeles lobby route.'});if(version!==routeVersion)return;}
      incident=loaded;property=catalog.properties.find(p=>p.id===incident.propertyId);
      if(!property)throw new Error('This incident’s source property is missing from the local catalog.');
      if(hash.startsWith('preview/')){renderPacket(incident,property,false);return;}caller?renderCaller():renderCase();startPoll();return;
    }
    recent=(await api('/incidents')).incidents;if(version!==routeVersion)return;home();
  }catch(error){if(version!==routeVersion)return;main.innerHTML=`<div class="error-screen"><h1>Couldn’t open this workspace.</h1><p>${e(error.message)}</p><a class="button primary" href="/live.html">Return to property search</a></div>`;} finally {
    if(version===routeVersion){
      main.removeAttribute('aria-busy');
      const title=hash.startsWith('caller/')?'Caller conversation':hash.startsWith('packet/')||hash.startsWith('preview/')?'Responder packet':incident?property?.name:'Dispatch workspace';
      document.title=`${title || 'Dispatch workspace'} · Pinpoint`;
      document.querySelectorAll('.topbar nav a').forEach(a=>a.removeAttribute('aria-current'));
      document.querySelector(property?.id===LA_DEMO_ID&&incident?'.topbar a[data-nav=los-angeles]':property?.id===PRINCETON_ID&&incident?'.topbar a[data-nav=princeton]':'.topbar a[data-nav=dispatch]')?.setAttribute('aria-current','page');
    }
  }
}
function startPoll() {
  polling=setInterval(async()=>{
    const preservesComposer=isSpatial(property)&&location.hash.startsWith('#incident/');
    if(!incident||busy||(dialog.open&&!preservesComposer)||snapshot||(!preservesComposer&&(document.activeElement?.matches('textarea,input')||document.querySelector('#message')?.value.trim())))return;
    const id=incident.id,version=routeVersion;try{const next=await api('/incidents/'+encodeURIComponent(id));if(version===routeVersion&&!snapshot&&/^#(?:incident|caller)\//.test(location.hash)&&incident?.id===id&&next.updatedAt!==incident.updatedAt){incident=next;location.hash.startsWith('#caller/')?renderCaller():renderCase();}}catch{ /* Explicit refresh surfaces network failures without interrupting a draft. */ }
  },1500);
}
function newIncident() {
  openDialog('Start incident',`<p class="muted">${e(selected.name)} · ${e(selected.address)}</p><form id="new-form"><div class="field"><label for="intake-text">Caller’s account</label><textarea id="intake-text" name="callerText" maxlength="4000" placeholder="Record what the caller reports. Do not infer their location from the building record."></textarea></div><div class="field"><label for="intake-photo">Caller photo (optional)</label><input id="intake-photo" name="photo" type="file" accept="image/png,image/jpeg,image/webp"><span class="hint">PNG, JPEG, WebP · 2 MB maximum. Provide text or a photo to start.</span></div><div class="actions"><button class="primary" type="submit">Create local incident</button></div></form>`);
}
async function readPhoto(file) {
  if(!file)return undefined;
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>2*1024*1024)throw new Error('Choose a PNG, JPEG, or WebP image smaller than 2 MB.');
  const dataUrl=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('Could not read this photo.'));reader.readAsDataURL(file);});return {name:file.name,dataUrl};
}
function edit() {
  openDialog('Edit dispatcher review',`<p class="muted">Changes create a new revision and invalidate approval. Keep uncertainty explicit.</p><form id="edit-form" data-revision="${incident.revision}"><div class="form-grid"><div class="field"><label for="location">Caller location</label><textarea id="location" name="location" maxlength="1000" ${[PRINCETON_ID,LA_DEMO_ID].includes(property.id)?'readonly':''}>${e(incident.location)}</textarea></div><div class="field"><label for="route">Entrance, staging & route</label><textarea id="route" name="route" maxlength="10000" ${[PRINCETON_ID,LA_DEMO_ID].includes(property.id)?'readonly':''}>${e(incident.route)}</textarea></div><div class="field full"><label for="notes">Dispatcher notes</label><textarea id="notes" name="notes" maxlength="10000">${e(incident.notes)}</textarea></div><div class="field full"><label for="unknowns">Unconfirmed items · one per line</label><textarea id="unknowns" name="unconfirmed" maxlength="10000">${e((incident.unconfirmed||[]).join('\n'))}</textarea></div></div><div class="actions"><button class="primary" type="submit">Save new revision</button></div></form>`);
}
async function update(data,expectedRevision=incident.revision) {incident=await writeAPI('/incidents/'+incident.id,'PATCH',{expectedRevision,...data});dialog.close();renderCase();toast('Saved. This revision needs dispatcher approval.');}
function download(name,content,type) {const blob=new Blob([content],{type});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1500);}
async function portable() {
  let body=packetBody(incident,property);const holder=document.createElement('div');holder.innerHTML=body;
  for(const img of holder.querySelectorAll('img'))if(!img.src.startsWith('data:')){const response=await fetch(img.getAttribute('src'));if(!response.ok)throw new Error('An evidence image could not be embedded. Refresh and try again.');const blob=await response.blob();img.src=await new Promise(resolve=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.readAsDataURL(blob);});}
  const style='body{font:15px/1.55 Arial,sans-serif;color:#182026;max-width:960px;margin:32px auto;padding:0 24px}h1{font-size:28px}h2{font-size:18px}.destination{border-bottom:4px solid #c72535;padding-bottom:15px}.packet-grid,.facts{display:grid;grid-template-columns:1fr 1fr;gap:20px}.facts dd{margin:0;font-weight:bold}.facts dt,.muted,.hint,.photo-caption{color:#59636d;font-size:12px}.photo{max-width:100%;max-height:200px}.unconfirmed{padding:16px;background:#fff8e8}.packet-body section{margin:20px 0}p{white-space:pre-wrap}img{object-fit:contain}@media(max-width:550px){.packet-grid{display:block}}@media print{@page{size:A4;margin:12mm}body{font-size:10pt;margin:0;padding:0}.photo{max-height:110px}section{break-inside:avoid}}';
  download(`pinpoint-${incident.id}-r${incident.revision}.html`,`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pinpoint packet · ${e(property.name)}</title><style>${style}</style><body>${holder.innerHTML}</body></html>`,'text/html');toast('Portable packet downloaded with embedded evidence images.');
}
async function copyLink(kind) {
  const url=new URL('/live.html',location.href);url.hash=kind==='caller'?`caller/${incident.id}`:`packet/${incident.packetId||snapshot?.id}`;
  try{await navigator.clipboard.writeText(url.href);toast('Local link copied. It works on this computer while the server runs.');}catch{openDialog('Local link',`<label for="copy-link">Copy this link</label><input id="copy-link" readonly value="${e(url.href)}"><p class="hint">Open in another browser on this computer. It is not a public link.</p>`);dialog.querySelector('input').select();}
}
main.addEventListener('input',ev=>{if(ev.target.id==='search')renderList();});
main.addEventListener('change',ev=>{if(ev.target.id==='city')renderList();if(ev.target.id==='caller-photo')action(async()=>{const photo=await readPhoto(ev.target.files[0]);if(photo){incident=await writeAPI('/incidents/'+incident.id,'PATCH',{expectedRevision:incident.revision,photo});renderCaller();toast('Photo attached locally. No matching was performed.');}});});
document.addEventListener('click',ev=>{
  if(ev.target.closest('a.skip')){ev.preventDefault();main.focus();return;}
  if(busy&&ev.target.closest('a')){ev.preventDefault();return;}
  const b=ev.target.closest('[data-action]');if(!b)return;
  const name=b.dataset.action;
  if(name==='close'){dialog.close();return;}
  if(name==='select'){selected=catalog.properties.find(p=>p.id===b.dataset.property);renderList();renderProperty();if(matchMedia('(max-width: 800px)').matches){document.querySelector('#property-detail').focus({preventScroll:true});document.querySelector('#property-detail').scrollIntoView({block:'start'});}else document.querySelector(`[data-property="${CSS.escape(selected.id)}"]`)?.focus({preventScroll:true});return;}
  if(name==='clear-search'){document.querySelector('#search').value='';document.querySelector('#city').value='';renderList();document.querySelector('#search').focus();return;}
  if(name==='new'){if(selected.id===PRINCETON_ID){location.hash='princeton';return;}newIncident();return;}
  if(name==='edit'){edit();return;}
  if(name==='image'){const draft=document.querySelector('#message')?.value||'';chosenImage=Number(b.dataset.index);renderCase();document.querySelector('#message').value=draft;document.querySelector(`[data-action=image][data-index="${chosenImage}"]`)?.focus({preventScroll:true});return;}
  if(name==='preview'){location.hash='preview/'+incident.id;return;}
  if(name==='scroll-review'){document.querySelector('#review')?.scrollIntoView({behavior:'instant'});return;}
  if(name==='scroll-chat'){document.querySelector('#conversation')?.focus();document.querySelector('#conversation')?.scrollIntoView({behavior:'instant'});return;}
  if(name==='print'){window.print();return;}
  if(name==='markdown'){download(`pinpoint-${incident.id}-r${incident.revision}.md`,packetText(incident,property),'text/markdown');return;}
  if(name==='stop-speech'){window.speechSynthesis?.cancel();return;}
  if(name==='speak'){
    const voice=window.speechSynthesis?.getVoices().find(v=>v.localService&&v.lang.startsWith('en'));
    if(!voice){toast('No offline English voice is available in this browser. Use the packet text instead.');return;}
    speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(`${property.name}. ${property.address}. Caller location: ${incident.location||'not confirmed'}. Access: ${incident.route||'not confirmed'}. ${incident.notes||''}. This is a local demonstration packet.`);utterance.voice=voice;speechSynthesis.speak(utterance);toast('Reading the brief using an offline system voice.');return;
  }
  if(name==='reject'){openDialog('Request changes',`<form id="reject-form" data-revision="${incident.revision}"><div class="field"><label for="reason">Reason for rejection</label><textarea id="reason" name="reason" required maxlength="2000"></textarea></div><div class="actions"><button class="primary" type="submit">Reject this revision</button></div></form>`);return;}
  if(name==='import'){openDialog('Import team packet',`<p class="muted">Import packet.md as reviewable text. HTML stays inert. This does not validate its claims or connect the agent.</p><form id="import-form" data-revision="${incident.revision}"><div class="field"><label for="packet-file">Markdown or text file · up to 100 KB</label><input id="packet-file" type="file" accept=".md,.txt,text/plain,text/markdown" required></div><div class="actions"><button class="primary">Import for review</button></div></form>`);return;}
  action(async()=>{
    if(name==='source'&&b.dataset.property===PRINCETON_ID){const record=await api('/spatial/model');openDialog('Princeton scan sources',`<p>Rabinowitz Hall · Matterport scan P3S6VUTP3qB</p><pre class="record">${e(JSON.stringify(record.provenance,null,2))}</pre>`);return;}
    if(name==='source'){const p=catalog.properties.find(v=>v.id===b.dataset.property);const url=safeLink(p.source?.recordUrl||`/data/records/${p.id}.json`);const response=await fetch(url);if(!response.ok)throw new Error('Source record could not be loaded.');const record=await response.json();openDialog('CoStar source record',`<p class="muted">Record ${e(p.id)}. Values are from the supplied dataset; they do not verify present conditions.</p><pre class="record">${e(JSON.stringify(record,null,2))}</pre><a class="record-link" href="${e(url)}" target="_blank">Open original local JSON</a>`);}
    if(name==='refresh'){if(!catalog){location.reload();return;}const draft=document.querySelector('#message')?.value||'';await navigate();const input=document.querySelector('#message');if(input)input.value=draft;}
    if(name==='approve'){incident=await writeAPI('/incidents/'+incident.id+'/decision','POST',{expectedRevision:incident.revision,decision:'approved'});renderCase();toast('Approved snapshot saved. A connected messaging bridge can now send it to the caller.');}
    if(name==='copy-packet')await copyLink('packet');
    if(name==='copy-caller')await copyLink('caller');
    if(name==='html')await portable();
  }, b);
});
document.addEventListener('submit',ev=>{
  const form=ev.target;ev.preventDefault();action(async()=>{
    if(form.id==='new-form'){const photo=await readPhoto(form.querySelector('input[type=file]').files[0]);const callerText=form.elements.callerText.value.trim();if(!callerText&&!photo)throw new Error('Enter the caller’s account or attach a photo.');const item=await writeAPI('/incidents','POST',{propertyId:selected.id,callerText,photo});dialog.close();location.hash='incident/'+item.id;}
    if(form.id==='edit-form'){await update({location:form.elements.location.value,route:form.elements.route.value,notes:form.elements.notes.value,unconfirmed:form.elements.unconfirmed.value.split('\n').map(x=>x.trim()).filter(Boolean)},Number(form.dataset.revision));}
    if(form.id==='message-form'){const text=form.elements.message.value.trim();if(!text)return;incident=await writeAPI('/incidents/'+incident.id+'/messages','POST',{expectedRevision:incident.revision,role:form.dataset.role,text});form.dataset.role==='caller'?renderCaller():renderCase();document.querySelector('#message')?.focus({preventScroll:true});const log=document.querySelector('#chat-log');if(log)log.scrollTop=log.scrollHeight;toast('Message saved to the local conversation.');}
    if(form.id==='reject-form'){incident=await writeAPI('/incidents/'+incident.id+'/decision','POST',{expectedRevision:Number(form.dataset.revision),decision:'rejected',reason:form.elements.reason.value.trim()});dialog.close();renderCase();toast('Changes requested. Edit the packet to reopen review.');}
    if(form.id==='import-form'){const file=form.querySelector('input').files[0];if(file.size>100*1024)throw new Error('Choose a packet smaller than 100 KB.');await update({packetMarkdown:await file.text()},Number(form.dataset.revision));}
  }, ev.submitter);
});
window.addEventListener('hashchange',navigate);
window.addEventListener('beforeunload',()=>window.speechSynthesis?.cancel());
try {catalog=await api('/catalog');selected=catalog.properties.find(p=>p.id===LA_DEMO_ID)||catalog.properties[0];await navigate();}catch(error){main.innerHTML=`<div class="error-screen"><h1>Start the real-data service.</h1><p>${e(error.message)}</p><p>From the app folder, run <code>npm run dev:real</code> and open its local URL.</p><button data-action="refresh">Try again</button></div>`;}
