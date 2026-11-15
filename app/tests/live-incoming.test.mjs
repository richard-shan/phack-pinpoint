import test from 'node:test';
import assert from 'node:assert/strict';
import {createIncomingTracker} from '../src/live-incoming.js';

test('detects new and replaced photos without reopening historical incidents',()=>{
  const track=createIncomingTracker();
  assert.deepEqual(track([{id:'a',photo:{name:'old',version:'one'}},{id:'b',photo:null}]),[]);
  assert.deepEqual(track([{id:'a',photo:{name:'old',version:'one'}},{id:'b',photo:null}]),[]);
  assert.deepEqual(track([{id:'b',photo:{name:'photo',version:'two'}},{id:'a',photo:{name:'old',version:'one'}}]).map(i=>i.id),['b']);
  assert.deepEqual(track([{id:'b',photo:{name:'photo',version:'three'}},{id:'c',photo:{name:'photo',version:'four'}}]).map(i=>i.id),['b','c']);
  assert.deepEqual(track([{id:'b',photo:{name:'photo',version:'three'}},{id:'c',photo:{name:'photo',version:'four'}}]),[]);
});
