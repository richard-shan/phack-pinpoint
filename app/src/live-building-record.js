import {escapeHTML as e} from './live-utils.js';

const labels = {sqft:'Building area',latlon:'Coordinates (latitude, longitude)',postcode:'Postal code',stories:'Floors',use_type:'Property type',use_subtype:'Property subtype',building_id:'Property ID',matterport_model_ids:'Matterport scans',year_built:'Year built',year_renovated:'Year renovated',floor_names:'Floor names',sprinkler_type:'Sprinkler type'};
const words = value => String(value).replace(/([a-z])([A-Z])/g,'$1 $2').replace(/_/g,' ');
const label = key => labels[key] || words(key).replace(/^./,c=>c.toUpperCase());

function valueHTML(value, key) {
  if(value == null || value === '' || (typeof value === 'object' && !Object.keys(value).length)) return '<span class="muted">Not recorded</span>';
  if(key === 'latlon' && Array.isArray(value)) return e(value.join(', '));
  if(Array.isArray(value)) return `<ul>${value.map(item=>`<li>${valueHTML(item,key)}</li>`).join('')}</ul>`;
  if(typeof value === 'object') return renderBuildingRecord(value);
  if(typeof value === 'boolean') return value ? 'Yes' : 'No';
  if(key === 'sqft') return `${e(Number(value).toLocaleString('en-US'))} sq ft`;
  if(typeof value === 'string' && /^https?:\/\//.test(value)) return `<a href="${e(value)}" target="_blank" rel="noopener noreferrer">View ${key === 'photo' ? 'building photo' : 'source'}</a>`;
  return e(typeof value === 'string' && ['use_type','use_subtype','role'].includes(key) ? words(value) : value);
}

export function renderBuildingRecord(record) {
  if(!record || typeof record !== 'object') return `<p>${valueHTML(record,'')}</p>`;
  return `<dl class="building-record">${Object.entries(record).map(([key,value])=>`<div><dt>${e(label(key))}</dt><dd>${valueHTML(value,key)}</dd></div>`).join('')}</dl>`;
}
