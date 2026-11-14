import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { ServiceError } from './local-store.mjs';

export const aerialBounds = { west:-74.65655, south:40.34845, east:-74.65445, north:40.34972 };
export const aerialCoverageMessage = 'Vexcel sponsor coverage includes Los Angeles, Houston and Princeton (expanded November 14).';

// The sponsor quickstart uses EPSG:4326, longitude-first WKT and X-API-KEY.
export function orthoRequest({west,south,east,north}, width=1400, height=1400) {
  if (![west,south,east,north].every(Number.isFinite) || west>=east || south>=north || west < -180 || east > 180 || south < -85 || north > 85) throw new ServiceError(400,'INVALID_BOUNDS','Invalid aerial bounds.');
  return {wkt:`POLYGON((${west} ${south},${east} ${south},${east} ${north},${west} ${north},${west} ${south}))`,srid:4326,layer:'urban,urban-r,wide-area,urban-g',crop:'bounding_box','image-format':'jpeg','destination-dimensions':`${width},${height}`,attribution:'true'};
}

export function vexcelService(root, fetchImage=fetch) {
  return {
    config() { return {provider:'Vexcel',available:true,imageUrl:'/api/aerial/princeton.jpg',bounds:aerialBounds,message:aerialCoverageMessage,sourceUrl:'https://beta-app.matterport.com/2026-princeton-hackathon/quickstart'}; },
    async extract(bounds, width=1400, height=1400) {
      const body=orthoRequest(bounds,width,height);
      const env=await readFile(resolve(root,'.env.local'),'utf8').catch(()=> '');
      const key=process.env.VEXCEL_API_KEY || env.match(/^VEXCEL_API_KEY=([^\r\n]*)/m)?.[1]?.trim().replace(/^(['"])(.*)\1$/,'$2');
      if(!key)throw new ServiceError(503,'VEXCEL_KEY_MISSING','Configure VEXCEL_API_KEY on the local server to load sponsor imagery.');
      let response;
      try { response=await fetchImage('https://api.vexcelgroup.com/v2/ortho/extract',{method:'POST',headers:{'X-API-KEY':key,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(12000)}); }
      catch { throw new ServiceError(502,'VEXCEL_UNAVAILABLE','Vexcel imagery could not load. Retry shortly.'); }
      if(!response.ok)throw new ServiceError(response.status===403?403:502,'VEXCEL_UNAVAILABLE',response.status===403?'Vexcel denied imagery access. Check key permissions and sponsor coverage.':'Vexcel imagery could not load. Retry shortly.');
      if(!response.headers.get('content-type')?.startsWith('image/jpeg'))throw new ServiceError(502,'VEXCEL_INVALID_IMAGE','Vexcel did not return a JPEG image.');
      return Buffer.from(await response.arrayBuffer());
    },
  };
}
