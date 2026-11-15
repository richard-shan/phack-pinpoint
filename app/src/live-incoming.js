// Observe photo arrivals without reopening old incidents on initial page load.
export function createIncomingTracker() {
  let initialized=false;
  const seen=new Map();
  return incidents=>{
    const arrivals=[];
    for(const incident of incidents){
      const photo=incident.photo;
      const version=photo ? (photo.version || photo.name) : null;
      if(initialized && version && seen.get(incident.id)!==version)arrivals.push(incident);
      seen.set(incident.id,version);
    }
    initialized=true;
    return arrivals;
  };
}
