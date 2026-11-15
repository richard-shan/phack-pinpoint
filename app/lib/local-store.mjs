import { mkdir, readFile, rename, writeFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

export class ServiceError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}
const fail = (status, code, message) => { throw new ServiceError(status, code, message); };
const clone = value => structuredClone(value);
const now = () => new Date().toISOString();
const idPattern = /^(inc|pkt)-[a-f0-9-]{36}$/;
const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

function object(value, allowed) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(400, 'INVALID_BODY', 'A JSON object is required.');
  if (Object.keys(value).some(key => !allowed.includes(key))) fail(400, 'UNKNOWN_FIELD', 'The request contains an unsupported field.');
}
function text(value, label, max, required = false) {
  if (typeof value !== 'string' || value.length > max || (required && !value.trim())) fail(400, 'INVALID_FIELD', `${label} must be ${required ? 'nonempty text' : 'text'} of at most ${max} characters.`);
  return value.trim();
}
function unknowns(value) {
  if (!Array.isArray(value) || value.length > 50) fail(400, 'INVALID_FIELD', 'Unconfirmed items must be an array of at most 50 strings.');
  return value.map(v => text(v, 'Unconfirmed item', 1000, true));
}
function photo(value) {
  if (value == null) return null;
  object(value, ['dataUrl', 'name']);
  const name = text(value.name, 'Photo name', 160, true);
  if (typeof value.dataUrl !== 'string') fail(400, 'INVALID_PHOTO', 'Photo data is required.');
  const match = /^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]*={0,2})$/.exec(value.dataUrl);
  if (!match || match[2].length % 4) fail(400, 'INVALID_PHOTO', 'Use a PNG, JPEG, or WebP image encoded as a base64 data URL.');
  const bytes = Buffer.from(match[2], 'base64');
  if (bytes.length > 2 * 1024 * 1024) fail(413, 'PHOTO_TOO_LARGE', 'Photos must be 2 MB or smaller.');
  const valid = match[1] === 'image/png' ? bytes.length >= 24 && bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])) && bytes.subarray(12,16).toString() === 'IHDR'
    : match[1] === 'image/jpeg' ? bytes.length >= 4 && bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255 && bytes.at(-2) === 255 && bytes.at(-1) === 217
      : bytes.length >= 16 && bytes.subarray(0,4).toString() === 'RIFF' && bytes.subarray(8,12).toString() === 'WEBP';
  if (!valid) fail(400, 'INVALID_PHOTO', 'The image contents do not match the declared image type.');
  return { name, dataUrl: `data:${match[1]};base64,${bytes.toString('base64')}` };
}

const PLATFORMS = { imessage: 'iMessage', telegram: 'Telegram', sms: 'SMS' };
function finite(value, label, min, max) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) fail(400, 'INVALID_FIELD', `${label} is out of range.`);
  return value;
}
// Where a messaged-in incident came from, and the caller's device location.
function channel(value) {
  if (value == null) return null;
  object(value, ['platform', 'handle', 'location']);
  if (!Object.hasOwn(PLATFORMS, value.platform)) fail(400, 'INVALID_FIELD', 'Channel platform must be imessage, telegram, or sms.');
  const out = { platform: value.platform, handle: text(value.handle ?? '', 'Channel handle', 200) };
  if (value.location != null) {
    object(value.location, ['building_id', 'latitude', 'longitude', 'address', 'distanceM', 'source']);
    out.location = {
      latitude: finite(value.location.latitude, 'Latitude', -90, 90),
      longitude: finite(value.location.longitude, 'Longitude', -180, 180),
      address: text(value.location.address ?? '', 'Location address', 300),
      distanceM: value.location.distanceM == null ? null : finite(value.location.distanceM, 'Distance', 0, 100000),
      source: text(value.location.source ?? 'simulated-device', 'Location source', 60),
    };
  }
  return out;
}

export function createLocalStore({ directory, getCatalog }) {
  let pending = Promise.resolve();
  const run = task => { const result = pending.then(task); pending = result.catch(() => {}); return result; };
  const file = id => { if (!idPattern.test(id)) fail(404, 'NOT_FOUND', 'Record not found.'); return join(directory, `${id}.json`); };
  const read = async id => {
    try { return JSON.parse(await readFile(file(id), 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') fail(404, 'NOT_FOUND', 'Record not found.'); throw error; }
  };
  async function atomic(id, value) {
    await mkdir(directory, { recursive: true, mode: 0o700 });
    const destination = file(id);
    const temp = `${destination}.${randomUUID()}.tmp`;
    try { await writeFile(temp, JSON.stringify(value), { mode: 0o600, flag: 'wx' }); await rename(temp, destination); }
    finally { await unlink(temp).catch(() => {}); }
  }
  async function current(id, revision) {
    if (!id.startsWith('inc-')) fail(404, 'NOT_FOUND', 'Incident not found.');
    const incident = await read(id);
    if (!Number.isSafeInteger(revision) || revision < 1) fail(400, 'INVALID_REVISION', 'expectedRevision must be a positive integer.');
    if (incident.revision !== revision) fail(409, 'REVISION_CONFLICT', 'This incident changed in another window. Reload it before saving.');
    return incident;
  }
  function revise(incident, event) {
    incident.revision += 1;
    incident.updatedAt = now();
    incident.status = 'review';
    incident.decision = null;
    incident.packetId = null;
    incident.events.push({ type: event.type, text: event.text, at: incident.updatedAt });
    if (incident.events.length > 500) incident.events = incident.events.slice(-500);
    return incident;
  }
  return {
    async list() {
      await pending;
      const { readdir } = await import('node:fs/promises');
      let files;
      try { files = await readdir(directory); } catch (error) { if (error.code === 'ENOENT') return []; throw error; }
      const incidents = await Promise.all(files.filter(name => /^inc-[a-f0-9-]{36}\.json$/.test(name)).map(name => read(name.slice(0, -5))));
      return incidents.sort((a,b) => b.updatedAt.localeCompare(a.updatedAt)).map(({ photo: image, ...incident }) => ({ ...incident, photo: image ? { name: image.name } : null }));
    },
    async get(id) { await pending; if (!id.startsWith('inc-')) fail(404, 'NOT_FOUND', 'Incident not found.'); return clone(await read(id)); },
    async photo(id) {
      await pending; if (!id.startsWith('inc-')) fail(404, 'NOT_FOUND', 'Incident not found.');
      const image = (await read(id)).photo;
      if (!image) fail(404, 'NOT_FOUND', 'This incident has no photo.');
      const [, type, data] = /^data:(image\/[a-z]+);base64,(.*)$/.exec(image.dataUrl);
      return { type, bytes: Buffer.from(data, 'base64') };
    },
    async packet(id) { await pending; if (!id.startsWith('pkt-')) fail(404, 'NOT_FOUND', 'Packet not found.'); return clone(await read(id)); },
    create(body) { return run(async () => {
      object(body, ['propertyId', 'callerText', 'photo', 'channel']);
      const via = channel(body.channel);
      const propertyId = text(body.propertyId, 'Property ID', 100, true);
      const callerText = text(body.callerText ?? '', 'Caller text', 4000);
      const image = photo(body.photo);
      if (!callerText && !image) fail(400, 'EMPTY_INTAKE', 'Add a caller message or photo.');
      const catalog = await getCatalog();
      const property = catalog.properties.find(p => String(p.id) === propertyId);
      if (!property) fail(400, 'UNKNOWN_PROPERTY', 'Choose a property from the verified catalog.');
      const timestamp = now();
      const incident = {
        id: `inc-${randomUUID()}`, propertyId, revision: 1, status: 'review', createdAt: timestamp, updatedAt: timestamp,
        callerText, photo: image, notes: '', unconfirmed: ['Exact room and floor', 'Entry and route', 'Hazards on scene'],
        location: '', route: '', packetMarkdown: '', messages: callerText ? [{ id: randomUUID(), role: 'caller', text: callerText, at: timestamp }] : [],
        events: [{ type: 'intake', text: 'Call received.', at: timestamp }, { type: 'record', text: `Building record loaded (${propertyId}).`, at: timestamp }],
        decision: null, packetId: null,
      };
      if (via) {
        incident.channel = via;
        const who = via.handle ? ` from ${via.handle}` : '';
        incident.events = [{ type: 'intake', text: `Photo received via ${PLATFORMS[via.platform]}${who}.`, at: timestamp },
          ...(via.location ? [{ type: 'location', text: `Device location: ${via.location.address || `${via.location.latitude}, ${via.location.longitude}`}.`, at: timestamp }] : []),
          incident.events[1]];
      }
      await atomic(incident.id, incident); return clone(incident);
    }); },
    update(id, body) { return run(async () => {
      object(body, ['expectedRevision', 'notes', 'unconfirmed', 'location', 'route', 'packetMarkdown', 'photo', 'updatedBy', 'court']);
      if (own(body, 'updatedBy') && !['agent', 'dispatcher'].includes(body.updatedBy)) fail(400, 'INVALID_FIELD', 'updatedBy must be agent or dispatcher.');
      if (!Object.keys(body).some(key => key !== 'expectedRevision' && key !== 'updatedBy')) fail(400, 'EMPTY_UPDATE', 'No changes were supplied.');
      const incident = await current(id, body.expectedRevision);
      for (const [key, max] of [['notes', 10000], ['location', 1000], ['route', 10000], ['packetMarkdown', 102400]]) if (own(body,key)) incident[key] = text(body[key], key, max);
      if (own(body, 'photo')) incident.photo = photo(body.photo);
      if (own(body, 'court')) {              // the evidence court's ruling, shown as received
        if (!body.court || typeof body.court !== 'object' || Array.isArray(body.court) || JSON.stringify(body.court).length > 60000) fail(400, 'INVALID_FIELD', 'court must be an object under 60 KB.');
        incident.court = clone(body.court);
      }
      if (own(body, 'unconfirmed')) incident.unconfirmed = unknowns(body.unconfirmed);
      revise(incident, { type: 'edit', text: own(body, 'photo') ? 'Caller photo updated.' : own(body, 'court') ? (body.court.overruled ? 'Evidence court overruled the photo match.' : 'Evidence court agreed with the photo match.') : body.updatedBy === 'agent' ? 'Packet updated.' : 'Edited by dispatcher.' });
      await atomic(id, incident); return clone(incident);
    }); },
    setRoute(id, revision, spatialRoute, route, location) { return run(async () => {
      const incident = await current(id, revision);
      incident.spatialRoute = clone(spatialRoute);
      incident.route = route;
      incident.location = location;
      revise(incident, { type: 'route', text: `Route updated. ${spatialRoute.reason || spatialRoute.message}` });
      incident.spatialRoute.revision = incident.revision;
      await atomic(id, incident); return clone(incident);
    }); },
    message(id, body) { return run(async () => {
      object(body, ['expectedRevision', 'role', 'text']);
      if (!['caller', 'dispatcher', 'auto'].includes(body.role)) fail(400, 'INVALID_ROLE', 'Message role must be caller, dispatcher, or auto.');
      const messageText = text(body.text, 'Message', 4000, true);
      const incident = await current(id, body.expectedRevision);
      if (incident.messages.length >= 200) fail(400, 'MESSAGE_LIMIT', 'This local incident has reached its 200-message limit.');
      if (body.role === 'auto') {            // a record of an automatic text: keeps the current review state
        incident.updatedAt = now();
        incident.messages.push({ id: randomUUID(), role: 'auto', text: messageText, at: incident.updatedAt });
        incident.events.push({ type: 'message', text: 'Auto-reply sent.', at: incident.updatedAt });
        await atomic(id, incident); return clone(incident);
      }
      revise(incident, { type: 'message', text: body.role === 'auto' ? 'Auto-reply sent.' : body.role === 'caller' ? 'Caller message.' : 'Message sent to caller.' });
      incident.messages.push({ id: randomUUID(), role: body.role, text: messageText, at: incident.updatedAt });
      await atomic(id, incident); return clone(incident);
    }); },
    decide(id, body) { return run(async () => {
      object(body, ['expectedRevision', 'decision', 'reason', 'correctedRoomId']);
      if (!['approved', 'rejected'].includes(body.decision)) fail(400, 'INVALID_DECISION', 'Decision must be approved or rejected.');
      const reason = text(body.reason ?? '', 'Reason', 2000, body.decision === 'rejected');
      const correctedRoomId = text(body.correctedRoomId ?? '', 'Corrected room', 500, false);
      const incident = await current(id, body.expectedRevision);
      if (incident.status !== 'review') fail(409, 'DECISION_EXISTS', 'This revision already has a decision. Edit it before reviewing again.');
      incident.status = body.decision;
      incident.updatedAt = now();
      incident.decision = { kind: body.decision, revision: incident.revision, reason, correctedRoomId, at: incident.updatedAt };
      incident.events.push({ type: 'decision', text: body.decision === 'approved' ? 'Confirmed by dispatcher.' : `Returned: ${reason}`, at: incident.updatedAt });
      if (body.decision === 'approved') {
        const catalog = await getCatalog();
        const property = catalog.properties.find(p => String(p.id) === incident.propertyId);
        if (!property) fail(409, 'PROPERTY_UNAVAILABLE', 'The source property is no longer available. Restore the catalog before approving.');
        incident.packetId = `pkt-${randomUUID()}`;
        await atomic(incident.packetId, { id: incident.packetId, approvedAt: incident.updatedAt, incident: clone(incident), property: clone(property) });
      }
      await atomic(id, incident); return clone(incident);
    }); },
  };
}
