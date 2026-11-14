export const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const known = value => value === null || value === undefined || value === '' ? 'Not provided' : String(value);
export function safeImage(url) {
  return typeof url === 'string' && !url.split('/').some(part => part === '.' || part === '..') && (/^\/assets\/real\/[a-zA-Z0-9_./-]+$/.test(url) || /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(url)) ? url : '';
}
export function safeLink(url) {
  if (typeof url !== 'string' || url.split('/').some(part => part === '.' || part === '..')) return '';
  if (/^\/(?:data\/records|assets\/real)\/[a-zA-Z0-9_.\/-]+$/.test(url)) return url;
  try { const u = new URL(url); return u.protocol === 'https:' ? u.href : ''; } catch { return ''; }
}
// Deliberately small Markdown subset: all raw HTML is text, never markup.
export function renderMarkdown(text) {
  return String(text || '').split(/\r?\n/).map(line => {
    const h = line.match(/^(#{1,4})\s+(.+)$/);
    if (h) return `<h${h[1].length + 1}>${escapeHTML(h[2])}</h${h[1].length + 1}>`;
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) return `<p class="md-bullet">• ${escapeHTML(bullet[1])}</p>`;
    return line ? `<p>${escapeHTML(line)}</p>` : '';
  }).join('');
}
export function sourceLabel(property) {
  return property.source?.file === 'Matterport public scan'
    ? `Matterport scan ${property.models?.[0]?.id || ''}`.trim()
    : `CoStar ${property.id}`;
}
export function packetText(incident, property) {
  return `# Pinpoint — responder review packet\n\n${property.name}\n${property.address}\n${property.city}, ${property.state || ''} ${property.postalCode || ''}\n\nStatus: ${incident.status}; revision ${incident.revision}\nLocal review only. No emergency service or external delivery is connected.\n\n## Caller-reported location / dispatcher notes\n${incident.location || 'Caller location not confirmed.'}\n\n## Access and route\n${incident.route || 'Entrance, staging, and route not confirmed.'}\n\n## Building record — ${sourceLabel(property)}\nUse: ${known(property.useType)}\nStories: ${known(property.stories)}\nYear built: ${known(property.yearBuilt)}\nArea: ${known(property.sqft)} sq ft\n\n## Dispatcher notes\n${incident.notes || 'None recorded.'}\n\n## Not confirmed\n${(incident.unconfirmed || []).map(x => '- '+x).join('\n')}\n\n## Imported packet (requires source verification)\n${incident.packetMarkdown || 'No team packet imported.'}\n\n## Source\n${property.source?.file || 'commercial-property.jsonl'}; record ${property.id}\nThis is a source record, not confirmation of current site conditions.\n`;
}
