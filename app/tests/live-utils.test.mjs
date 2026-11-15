import test from 'node:test';
import assert from 'node:assert/strict';
import { escapeHTML, known, safeImage, safeLink, renderMarkdown, packetText } from '../src/live-utils.js';

test('imported Markdown never activates raw HTML, script, SVG, or Markdown image/link syntax', () => {
  const hostile = '# <img src=x onerror="alert(1)">\n<script>alert(1)</script>\n- <svg onload=alert(1)>\n[open](javascript:alert(1))\n![remote](https://outside.invalid/tracker.png)\n</p><iframe src="https://outside.invalid"></iframe>';
  const rendered = renderMarkdown(hostile);
  assert(!/<(?:script|img|svg|iframe|a)\b/i.test(rendered));
  assert(rendered.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
  assert(rendered.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
  assert(rendered.includes('[open](javascript:alert(1))'));
  assert(rendered.startsWith('<h2>'));
  assert.equal(escapeHTML(`&<>"'`),'&amp;&lt;&gt;&quot;&#39;');
});

test('source links admit HTTPS and intended local evidence while refusing executable or ambiguous schemes', () => {
  for (const url of ['javascript:alert(1)','data:text/html,<script>alert(1)</script>','file:///etc/passwd','http://outside.invalid/','//outside.invalid/','blob:https://outside.invalid/id',null,42]) assert.equal(safeLink(url),'',String(url));
  assert.equal(safeLink('https://example.org/model?id=abc'),'https://example.org/model?id=abc');
  assert.equal(safeLink('/data/records/350372.json'),'/data/records/350372.json');
  assert.equal(safeLink('/assets/real/reference.jpg'),'/assets/real/reference.jpg');
});

test('local evidence URL helpers reject traversal even before the server allowlist', () => {
  for (const path of ['/assets/real/../../live.js','/assets/real/../other.jpg','/assets/real/./reference.jpg']) assert.equal(safeImage(path),'',path);
  for (const path of ['/data/records/../catalog.json','/data/records/../../live.html','/assets/real/../other.jpg']) assert.equal(safeLink(path),'',path);
  assert.equal(safeImage('/assets/real/reference.jpg'),'/assets/real/reference.jpg');
});

test('images reject active formats and non-evidence remote URLs', () => {
  for (const url of ['javascript:alert(1)','data:image/svg+xml;base64,PHN2Zz4=','https://outside.invalid/photo.jpg','/data/records/350372.json','/assets/real/x.jpg" onerror="alert(1)',null]) assert.equal(safeImage(url),'',String(url));
  assert.equal(safeImage('data:image/png;base64,aGVsbG8='),'data:image/png;base64,aGVsbG8='); // File signatures are checked by the upload service, not this display helper.
});

test('packet export uses the supplied source snapshot and preserves unknowns without inventing metrics', () => {
  const property={id:'real-record-123',name:'Verified building',address:'12 Source Street',city:'Houston',state:'TX',postalCode:'77001',useType:'Office',stories:0,yearBuilt:null,sqft:null,source:{file:'supplied/commercial-property.jsonl'}};
  const incident={id:'case-1',status:'approved',revision:7,location:'Caller reports suite 200',route:'Dispatcher entered: confirm elevator access',notes:'Await caller confirmation',unconfirmed:['Door access unknown','No equipment confirmed'],packetMarkdown:'Team-supplied claim — unverified'};
  const output=packetText(incident,property);
  assert(output.includes('Verified building\n12 Source Street'));
  assert(output.includes('CoStar real-record-123'));
  assert(output.includes('supplied/commercial-property.jsonl; record real-record-123'));
  assert(output.includes('Status: approved; revision 7'));
  assert(output.includes('Stories: 0'));
  assert(output.includes('Year built: Not provided'));
  assert(output.includes('- Door access unknown\n- No equipment confirmed'));
  assert(output.includes('Imported packet (requires source verification)'));
  assert(!/demo|local review/i.test(output));
  assert(!output.includes('PP-1042'));
  assert(!output.includes('undefined'));
  const unknown=packetText({...incident,location:'',route:''},property);
  assert(unknown.includes('## Caller location\nUnknown'));
  assert(unknown.includes('## Entry and route\nNo route'));
  assert.equal(known(0),'0'); assert.equal(known(null),'Not provided');
  const scan=packetText(incident,{...property,id:'scan-building',source:{file:'Matterport public scan'},models:[{id:'scan-123'}]});
  assert(scan.includes('Building record — Matterport scan scan-123'));
  assert(!scan.includes('CoStar'));
});
