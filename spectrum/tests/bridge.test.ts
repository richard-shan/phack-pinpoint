import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Message, Space } from 'spectrum-ts';
import { PhotonConsoleBridge } from '../src/bridge.js';
import { ConsoleClient } from '../src/console.js';

test('a text then photo creates one photo-inferred incident with the caller text', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'spectrum-bridge-')); t.after(() => rmSync(dir, {recursive:true,force:true}));
  const calls: unknown[] = [], sent: string[] = [];
  const client = {
    async createFromPhoto(text: string, photo: unknown) { calls.push({text,photo}); return {id:'inc-demo',messages:[]}; },
  } as unknown as ConsoleClient;
  const space = { id:'chat-1', async send(text:string) {sent.push(text);} } as unknown as Space;
  const bridge = new PhotonConsoleBridge(client, undefined, undefined, join(dir,'state.json'));
  await bridge.onInbound(space, {direction:'inbound',content:{type:'text',text:'I fell by the stairs'}} as Message);
  assert.equal(calls.length,0);
  await bridge.onInbound(space, {direction:'inbound',content:{type:'attachment',mimeType:'image/jpeg',name:'photo.jpg',size:4,read:async()=>Buffer.from([255,216,255,217])}} as unknown as Message);
  assert.equal(calls.length,1);
  assert.equal((calls[0] as {text:string}).text,'I fell by the stairs');
  assert.match(sent.at(-1)!,/received/i);
});

test('uncertain match does not claim the incident was created and keeps text for retry', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'spectrum-bridge-')); t.after(() => rmSync(dir, {recursive:true,force:true}));
  const sent:string[]=[]; let received=''; let attempts=0;
  const client={async createFromPhoto(text:string){received=text; if(++attempts===1)throw Object.assign(new Error('uncertain'),{code:'DEMO_MATCH_UNCERTAIN'});return {id:'inc-demo',messages:[]};}} as unknown as ConsoleClient;
  const space={id:'chat-2',async send(text:string){sent.push(text);}} as unknown as Space;
  const bridge=new PhotonConsoleBridge(client,undefined,undefined,join(dir,'state.json'));
  const photo={direction:'inbound',content:{type:'attachment',mimeType:'image/jpeg',name:'photo.jpg',read:async()=>Buffer.from([255,216,255,217])}} as unknown as Message;
  await bridge.onInbound(space,{direction:'inbound',content:{type:'text',text:'Help'}} as Message);
  await bridge.onInbound(space,photo); assert.match(sent.at(-1)!,/could not identify/);
  await bridge.onInbound(space,photo); assert.equal(received,'Help'); assert.match(sent.at(-1)!,/received/i);
});

test('bridge posts the photo and caller text to the real local incident API', async t => {
  const { mkdirSync, writeFileSync } = await import('node:fs');
  const { createLocalServer } = await import('../../app/local-server.mjs');
  const dir=mkdtempSync(join(tmpdir(),'spectrum-http-')); const root=join(dir,'app');
  mkdirSync(join(root,'data'),{recursive:true});
  writeFileSync(join(root,'data/catalog.json'),JSON.stringify({properties:[{id:'6550930',unknowns:[]}]}));
  const server=createLocalServer({root,inferBuilding:async()=>({building_id:'6550930'})});
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{await new Promise<void>(resolve=>server.close(()=>resolve()));rmSync(dir,{recursive:true,force:true});});
  const address=server.address(); assert(address && typeof address!=='string');
  const client=new ConsoleClient(`http://127.0.0.1:${address.port}`);
  const bridge=new PhotonConsoleBridge(client,undefined,undefined,join(dir,'state.json'));
  const space={id:'real-api-chat',async send() {}} as unknown as Space;
  await bridge.onInbound(space,{direction:'inbound',content:{type:'text',text:'Near the lobby'}} as Message);
  await bridge.onInbound(space,{direction:'inbound',content:{type:'attachment',mimeType:'image/jpeg',name:'photo.jpg',read:async()=>Buffer.from([255,216,255,217])}} as unknown as Message);
  const incidents=await client.list(); assert.equal(incidents.length,1);
  const incident=await client.get(incidents[0]!.id);
  assert.equal(incident.propertyId,'6550930'); assert.equal(incident.messages[0]!.text,'Near the lobby'); assert.ok(incident.photo);
});
