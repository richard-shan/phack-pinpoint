import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const source = (await readFile(new URL('../ViewerSource/viewer.js', import.meta.url), 'utf8')).replace("import {routeComponentFactory} from './route-component.js';", 'const routeComponentFactory = () => {};');
const settle = async () => { for(let i=0;i<8;i++) await new Promise(resolve=>setImmediate(resolve)); };
async function harness() {
  const callbacks={}, messages=[], moves=[], timers=new Map(); let timerId=0;
  const observable = name => ({subscribe(callback){callbacks[name]=callback;return {cancel(){}};}});
  const nodes = [0,1,2].map(i=>({id:String(i),position:{x:i,y:0,z:0},floor:0,label:`Position ${i}`}));
  const component={inputs:{route:null}};
  const sdk={
    Scene:{register:async()=>{},createObjects:async()=>[{addNode:()=>({addComponent:()=>component}),start(){},stop(){}}]},
    Sweep:{data:observable('sweeps'),current:observable('current'),moveTo:async(id,options)=>moves.push({id,options})},
    App:{state:observable('app'),Phase:{PLAYING:'playing',ERROR:'error'}},
    Mode:{current:observable('mode'),Mode:{DOLLHOUSE:'dollhouse',INSIDE:'inside',FLOORPLAN:'floorplan'},moveTo:async()=>{}},
    Floor:{showAll:async()=>{}}, Camera:{TransitionType:{FLY:'fly',INSTANT:'instant'}}
  };
  const frame={addEventListener(event,callback){callbacks[event]=callback},contentWindow:{MP_SDK:{connect:async()=>sdk}}};
  const context={window:{webkit:{messageHandlers:{responder:{postMessage:p=>messages.push(p)}}},addEventListener(){}},document:{querySelector:()=>frame},fetch:async()=>({ok:true,json:async()=>({sdkKey:'test',graph:{modelId:'demo',nodes},route:{nodeIds:['0','1','2'],status:'ready'}})}),setTimeout:(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId},clearTimeout(id){timers.delete(id)},console};
  vm.runInNewContext(source,context); await settle(); await callbacks.load();
  const align = (offset=0)=>callbacks.sweeps.onCollectionUpdated(Object.fromEntries(nodes.map(n=>[n.id,{id:n.id,puckPosition:{...n.position,x:n.position.x+offset}}])));
  return {callbacks,messages,moves,component,sdk,timers,command:context.window.responder.command,align};
}
test('guidance stays unavailable until all route coordinates align and scan is playing',async()=>{
  const h=await harness(); h.callbacks.app({phase:'playing'}); await settle();
  assert.equal(h.messages.at(-1).ready,false); assert.equal(h.component.inputs.route,null);
  h.align(); await settle(); assert.equal(h.messages.at(-1).ready,true); assert.equal(h.component.inputs.route.status,'ready');
});
test('waypoint commands clamp at destination and respect Reduce Motion',async()=>{
  const h=await harness();h.align();h.callbacks.app({phase:'playing'});await settle();
  await h.command('inside',true); assert.equal(h.moves.at(-1).options.transition,'instant');
  await h.command('next');await h.command('next');await h.command('next');
  assert.equal(h.messages.at(-1).index,2);assert.equal(h.moves.at(-1).id,'2');assert.equal(h.messages.at(-1).busy,false);
  await h.command('previous');assert.equal(h.messages.at(-1).index,1);
});
test('misaligned scan clears arrows and disables navigation',async()=>{
  const h=await harness();h.align();h.callbacks.app({phase:'playing'});await settle();h.align(0.5);
  assert.equal(h.component.inputs.route,null);assert.equal(h.messages.at(-1).type,'error');
  await h.command('next');assert.equal(h.moves.length,0);
});

test('a hung viewer command reports retry instead of leaving controls pending',async()=>{
  const h=await harness();h.align();h.callbacks.app({phase:'playing'});await settle();
  h.sdk.Sweep.moveTo=()=>new Promise(()=>{});
  h.command('next');
  const watchdog=[...h.timers.values()].find(timer=>timer.ms===20000);
  assert.ok(watchdog);watchdog.fn();
  assert.equal(h.messages.at(-1).type,'error');
  assert.match(h.messages.at(-1).message,/Retry/);
  assert.equal(h.component.inputs.route,null);
});

test('manual exploration clears route state and returning restores it',async()=>{
 const h=await harness();h.align();h.callbacks.app({phase:'playing'});await settle();
 await h.command('next');await h.command('next');
 h.callbacks.current({id:'outside-the-route'});
 assert.equal(h.messages.at(-1).onRoute,false);assert.equal(h.messages.at(-1).index,2);
 await h.command('inside');assert.equal(h.messages.at(-1).onRoute,true);assert.equal(h.moves.at(-1).id,'2');
});
