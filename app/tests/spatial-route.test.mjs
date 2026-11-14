import test from 'node:test';
import assert from 'node:assert/strict';
import {planRoute} from '../src/live-route-model.js';
const graph={modelId:'P3S6VUTP3qB',defaultDestination:'c',entries:[{id:'entry',nodeId:'a',position:{x:-2,y:0,z:0}}],nodes:[{id:'a',floor:0,position:{x:0,y:0,z:0}},{id:'b',floor:0,position:{x:3,y:0,z:0}},{id:'c',floor:1,position:{x:3,y:3,z:0}},{id:'d',floor:0,position:{x:0,y:0,z:4}}],edges:[{id:'ab',a:'a',b:'b',kind:'walk'},{id:'bc',a:'b',b:'c',kind:'stairs'},{id:'ad',a:'a',b:'d',kind:'walk'},{id:'db',a:'d',b:'b',kind:'walk'}]};
test('continuous route begins at external anchor and follows explicit stair connection',()=>{
 const r=planRoute(graph);assert.deepEqual(r.nodeIds,['a','b','c']);assert.equal(r.segments[0].kind,'entry');assert.deepEqual(r.segments[0].points[0],graph.entries[0].position);assert.equal(r.segments.at(-1).kind,'stairs');assert.equal(r.meters,8);
});
test('blocking a corridor reroutes; blocking the only stair clears all stale geometry',()=>{
 assert.deepEqual(planRoute(graph,{blockedEdges:['ab']}).nodeIds,['a','d','b','c']);
 const r=planRoute(graph,{blockedEdges:['bc']});assert.equal(r.status,'unavailable');assert.deepEqual(r.segments,[]);
});
test('step-free hard-excludes stairs and requires explicitly confirmed elevator',()=>{
 assert.equal(planRoute(graph,{profile:'stretcher'}).status,'unavailable');
 assert.throws(()=>planRoute(graph,{connectors:[{a:'a',b:'c',kind:'elevator'}]}),/confirmation/);
 const r=planRoute(graph,{profile:'stretcher',connectors:[{a:'a',b:'c',kind:'elevator',confirmed:true}]});assert.equal(r.status,'ready');assert(!r.segments.some(s=>s.kind==='stairs'));assert.equal(r.segments[1].kind,'elevator');assert.equal(r.segments[1].points[1].y,3);
});
test('agent cannot submit disconnected, blocked, or wrong-entry paths',()=>{
 for(const nodeIds of [['b','c'],['a','c'],['a','unknown','c']])assert.throws(()=>planRoute(graph,{nodeIds}));
 assert.throws(()=>planRoute(graph,{nodeIds:['a','b','c'],blockedEdges:['ab']}));
 assert.throws(()=>planRoute(graph,{profile:'stretcher',nodeIds:['a','b','c']}));
 assert.equal(planRoute(graph,{nodeIds:['a','d','b','c'],updatedBy:'agent'}).updatedBy,'agent');
});
test('floor sightlines absent from traversal graph never become edges',()=>{
 const g=structuredClone(graph);g.nodes[0].neighbors=['c'];assert.deepEqual(planRoute(g).nodeIds,['a','b','c']);
});
