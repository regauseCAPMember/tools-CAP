(function(){

'use strict';
const R_EARTH_FT = 20925524.9;
let view, graphicsLayer, countyLayer, capGridLayer, capSubGridLayer, capHighlightLayer;
let EsriPointClass = null, EsriGraphicClass = null;
let points = [], live = null, liveHeadingAnchor = null, elt = null, liveWatch = null, lastLiveUpdate = 0, liveError = ''; // liveWatch can exist before the first GPS fix arrives
let deviceHeading = null, deviceHeadingSource = '', capGridVisible = true;
let eltCapGridLabel = '--', liveCapGridLabel = '--';
let lastEltCapGridKey = '', lastLiveCapGridKey = '';
let eltHighlightGraphic = null, liveHighlightGraphic = null;
const CAP_GRID_SERVICE = 'https://services9.arcgis.com/U0vgiXgwLpyQDfbW/ArcGIS/rest/services/Civil_Air_Patrol_Search_and_Rescue_SAR_Grids/FeatureServer';
const $ = id => document.getElementById(id);
const nowIso = () => new Date().toISOString();
const degNorm = d => ((Number(d)%360)+360)%360;
const rad = d => d*Math.PI/180, deg = r => r*180/Math.PI;
const typeName = t => t==='C'?'Circumcenter':t==='D'?'Directional':t==='E'?'ELT':'Track';
function clamp(v,min,max){return Math.max(min,Math.min(max,v));}
function fmtNum(v,n=5){return Number.isFinite(v)?v.toFixed(n):'--';}
function escapeCsv(v){v = String(v ?? ''); return /[",\n]/.test(v) ? '"'+v.replaceAll('"','""')+'"' : v;}

function parseCoord(text, isLat){
  if(text == null || String(text).trim()==='') return NaN;
  let s = String(text).trim().toUpperCase();
  let sign = /[SW]/.test(s) ? -1 : 1;
  s = s.replace(/[NSEW°'";,]/g,' ').replace(/\s+/g,' ').trim();
  const parts = s.split(' ').map(Number).filter(v=>!Number.isNaN(v));
  if(parts.length===0) return NaN;
  if(parts[0] < 0) sign = -1;
  const d = Math.abs(parts[0]);
  const m = Math.abs(parts[1] || 0);
  const sec = Math.abs(parts[2] || 0);
  let dd = sign * (d + m/60 + sec/3600);
  if(isLat && Math.abs(dd)>90) return NaN;
  if(!isLat && Math.abs(dd)>180) return NaN;
  return dd;
}
function coordToDMM(dd,isLat){
  if(!Number.isFinite(dd)) return '--';
  const hemi = isLat ? (dd>=0?'N':'S') : (dd>=0?'E':'W');
  const a = Math.abs(dd), d = Math.floor(a), m = (a-d)*60;
  return `${d} ${m.toFixed(5)} ${hemi}`;
}
function coordToDMS(dd,isLat){
  if(!Number.isFinite(dd)) return '--';
  const hemi = isLat ? (dd>=0?'N':'S') : (dd>=0?'E':'W');
  const a = Math.abs(dd), d = Math.floor(a), mf = (a-d)*60, m = Math.floor(mf), s = (mf-m)*60;
  return `${d} ${m} ${s.toFixed(2)} ${hemi}`;
}
function syncFromDmm(){ const lat=parseCoord($('latDmm').value,true), lon=parseCoord($('lonDmm').value,false); if(Number.isFinite(lat)) $('latDms').value=coordToDMS(lat,true); if(Number.isFinite(lon)) $('lonDms').value=coordToDMS(lon,false); }
function syncFromDms(){ const lat=parseCoord($('latDms').value,true), lon=parseCoord($('lonDms').value,false); if(Number.isFinite(lat)) $('latDmm').value=coordToDMM(lat,true); if(Number.isFinite(lon)) $('lonDmm').value=coordToDMM(lon,false); }

function refCenter(){ const all=[...points, ...(live?[live]:[])]; if(all.length===0) return {lat:33.68, lon:-79.0}; return {lat:all.reduce((a,p)=>a+p.lat,0)/all.length, lon:all.reduce((a,p)=>a+p.lon,0)/all.length}; }
function project(p, ref){ const lat0=rad(ref.lat); return {x:rad(p.lon-ref.lon)*Math.cos(lat0)*R_EARTH_FT, y:rad(p.lat-ref.lat)*R_EARTH_FT}; }
function unproject(q, ref){ const lat0=rad(ref.lat); return {lat:ref.lat+deg(q.y/R_EARTH_FT), lon:ref.lon+deg(q.x/(R_EARTH_FT*Math.cos(lat0)))}; }
function bearingVector(bearingDeg){ const b=rad(degNorm(bearingDeg)); return {x:Math.sin(b), y:Math.cos(b)}; }
function lineFromPointBearing(p, bearingDeg, ref, kind, source){ const q=project(p,ref), v=bearingVector(bearingDeg); return {p:q, v, kind, source, bearing:degNorm(bearingDeg), weight:1}; }
function bisectorLine(a,b,ref,source){ const pa=project(a,ref), pb=project(b,ref); const mid={x:(pa.x+pb.x)/2,y:(pa.y+pb.y)/2}; const chord={x:pb.x-pa.x,y:pb.y-pa.y}; const len=Math.hypot(chord.x,chord.y); if(len<20) return null; return {p:mid, v:{x:-chord.y/len,y:chord.x/len}, kind:'Circumcenter bisector', source, bearing:null, weight:.85}; }
function lineIntersection(l1,l2){ const det=l1.v.x*l2.v.y-l1.v.y*l2.v.x; if(Math.abs(det)<1e-8) return null; const dx=l2.p.x-l1.p.x, dy=l2.p.y-l1.p.y; const t=(dx*l2.v.y-dy*l2.v.x)/det; const u=(dx*l1.v.y-dy*l1.v.x)/det; return {x:l1.p.x+t*l1.v.x,y:l1.p.y+t*l1.v.y,t,u,angle:Math.abs(deg(Math.asin(clamp(det,-1,1))))}; }
function closestPointToLines(lines){
  let A=0,B=0,C=0,D=0,E=0; for(const l of lines){ const vx=l.v.x, vy=l.v.y, w=l.weight||1; const a=vy*vy*w, b=-vx*vy*w, c=vx*vx*w; A+=a; B+=b; C+=c; D+=(a*l.p.x+b*l.p.y); E+=(b*l.p.x+c*l.p.y); }
  const det=A*C-B*B; if(Math.abs(det)<1e-8) return null; const x=(D*C-B*E)/det, y=(A*E-B*D)/det; return {x,y};
}
function perpendicularDistance(pt,line){ const dx=pt.x-line.p.x, dy=pt.y-line.p.y; return Math.abs(dx*line.v.y-dy*line.v.x); }
function buildLines(ref){
  const lines=[]; const c=points.filter(p=>p.type==='C'), d=points.filter(p=>p.type==='D');
  for(const p of d){ const b = p.bearing; if(Number.isFinite(b)) lines.push(lineFromPointBearing(p,b,ref,typeName(p.type),p.id)); }
  if(c.length>=2){ for(let i=0;i<c.length;i++) for(let j=i+1;j<c.length;j++){ const l=bisectorLine(c[i],c[j],ref,`C${i+1}-C${j+1}`); if(l) lines.push(l); } }
  return lines;
}
function computeElt(){
  const ref=refCenter(), lines=buildLines(ref), dirCount=points.filter(p=>p.type==='D').length, cCount=points.filter(p=>p.type==='C').length;
  elt=null; let method='Not enough geometry', quality='Need 3 C, or 2 D, or 2 C + 2 D', rms=null, radius=null;
  if(lines.length>=2 && (dirCount>=2 || cCount>=3 || (cCount>=2 && dirCount>=2))){
    const q=closestPointToLines(lines); if(q){ elt={...unproject(q,ref), q, ref}; const errs=lines.map(l=>perpendicularDistance(q,l)); rms=Math.sqrt(errs.reduce((a,e)=>a+e*e,0)/errs.length); radius=rms*2; const worstAngle=minIntersectionAngle(lines); method = cCount>=3 && dirCount===0 ? 'Circumcenter' : cCount===0 ? 'Directional' : 'Hybrid'; quality = rms<500 ? 'Good' : rms<1500 ? 'Fair' : 'Poor / conflicting lines'; elt.method=method; elt.rms=rms; elt.radius=radius; elt.linesUsed=lines.length; elt.quality=quality; elt.minAngle=worstAngle; }
  }
  return {ref, lines, method, quality, rms, radius};
}
function minIntersectionAngle(lines){ let best=180; for(let i=0;i<lines.length;i++) for(let j=i+1;j<lines.length;j++){ const det=Math.abs(lines[i].v.x*lines[j].v.y-lines[i].v.y*lines[j].v.x); best=Math.min(best, Math.abs(deg(Math.asin(clamp(det,-1,1))))); } return best; }
function distanceBearing(a,b){ const lat1=rad(a.lat), lat2=rad(b.lat), dLat=lat2-lat1, dLon=rad(b.lon-a.lon); const x=dLon*Math.cos((lat1+lat2)/2)*R_EARTH_FT, y=dLat*R_EARTH_FT; const dist=Math.hypot(x,y); return {distanceFt:dist,bearing:degNorm(deg(Math.atan2(x,y)))}; }
function bestLiveHeading(pos){
  const cur = pos && pos.coords ? {lat:pos.coords.latitude, lon:pos.coords.longitude} : null;
  const gpsHeading = pos && pos.coords && Number.isFinite(pos.coords.heading) ? degNorm(pos.coords.heading) : NaN;
  if(Number.isFinite(gpsHeading)){
    liveHeadingAnchor = cur || liveHeadingAnchor;
    return {heading:gpsHeading, source:'GPS course'};
  }
  if(Number.isFinite(deviceHeading)){
    liveHeadingAnchor = cur || liveHeadingAnchor;
    return {heading:degNorm(deviceHeading), source:deviceHeadingSource || 'device compass'};
  }
  if(cur && !liveHeadingAnchor){
    liveHeadingAnchor = cur;
    return {heading: live && Number.isFinite(live.heading) ? live.heading : NaN, source: live?.headingSource || 'waiting for movement'};
  }
  if(cur && liveHeadingAnchor){
    const moved = distanceBearing(liveHeadingAnchor, cur);
    // Critical: do not move the heading anchor until motion exceeds the jitter threshold.
    // Otherwise each small GPS wobble becomes the new baseline and destroys course-over-ground heading.
    if(moved.distanceFt >= 25){
      liveHeadingAnchor = cur;
      return {heading:moved.bearing, source:'derived from movement'};
    }
  }
  return {heading: live && Number.isFinite(live.heading) ? live.heading : NaN, source: live?.headingSource || 'waiting for 25+ ft movement'};
}
function handleDeviceOrientation(ev){
  let h = NaN, src = '';
  if(Number.isFinite(ev.webkitCompassHeading)){ h = ev.webkitCompassHeading; src = 'iOS compass'; }
  else if(ev.absolute === true && Number.isFinite(ev.alpha)){ h = 360 - ev.alpha; src = 'device orientation'; }
  if(Number.isFinite(h)){ deviceHeading = degNorm(h); deviceHeadingSource = src; if(live && !Number.isFinite(live.heading)){ live.heading=deviceHeading; live.headingSource=src; updateAll(); } }
}
async function enableCompass(){
  try{
    if(window.DeviceOrientationEvent && typeof DeviceOrientationEvent.requestPermission === 'function'){
      const p = await DeviceOrientationEvent.requestPermission();
      if(p !== 'granted') return false;
    }
    window.addEventListener('deviceorientationabsolute', handleDeviceOrientation, true);
    window.addEventListener('deviceorientation', handleDeviceOrientation, true);
    return true;
  }catch(e){ console.warn('Compass/orientation unavailable', e); return false; }
}


function addPointFromForm(){ const lat=parseCoord($('latDmm').value,true) || parseCoord($('latDms').value,true); const lon=parseCoord($('lonDmm').value,false) || parseCoord($('lonDms').value,false); if(!Number.isFinite(lat)||!Number.isFinite(lon)){alert('Enter a valid latitude and longitude.');return;} addPoint(lat,lon); }
function addPoint(lat,lon, extra={}){ const type=$('method').value; let p={id:crypto.randomUUID(), type, lat, lon, time:nowIso(), notes:$('notes').value.trim(), ...extra}; if(type==='D'){ p.bearing=degNorm(Number($('bearing').value)); if(!Number.isFinite(p.bearing)){alert('Directional method requires a bearing to the ELT.');return;} }
  points.push(p); updateAll(); }
function clearEntry(){ ['latDmm','lonDmm','latDms','lonDms','bearing','notes'].forEach(id=>$(id).value=''); }
function updateMethodUi(autoFill=false){ const m=$('method').value; $('bearingBox').style.display=m==='D'?'block':'none'; $('bearingLabel').textContent='Bearing to ELT'; if(autoFill) populatePointEntryDefaults(); }
function renderTable(){ const tb=$('pointsTable').querySelector('tbody'); tb.innerHTML=''; for(const p of points){ const tr=document.createElement('tr'); const used=p.bearing; tr.innerHTML=`<td><span class="tag ${p.type.toLowerCase()}">${p.type}</span> ${typeName(p.type)}</td><td class="mono">${p.time}</td><td class="mono">${coordToDMM(p.lat,true)}</td><td class="mono">${coordToDMM(p.lon,false)}</td><td class="mono">${coordToDMS(p.lat,true)}</td><td class="mono">${coordToDMS(p.lon,false)}</td><td>${p.type==='C'?'--':fmtNum(p.bearing,1)}</td><td>${Number.isFinite(used)?fmtNum(used,1):'--'}</td><td>${p.notes||''}</td><td><button class="mini danger" data-del="${p.id}">Delete</button></td>`; tb.appendChild(tr); } tb.querySelectorAll('[data-del]').forEach(b=>b.onclick=()=>{points=points.filter(p=>p.id!==b.dataset.del);updateAll();}); }

function currentHeadingFromPosition(pos){
  if(pos && pos.coords && Number.isFinite(pos.coords.heading)) return degNorm(pos.coords.heading);
  if(live && Number.isFinite(live.heading)) return degNorm(live.heading);
  if(Number.isFinite(deviceHeading)) return degNorm(deviceHeading);
  return NaN;
}
function updateNotesWithCapGrid(label){
  const gridText = label && label !== '--' ? label : 'Lookup pending';
  const defaultNote = `CAP Grid: ${gridText}`;
  const current = $('notes').value.trim();
  if(!current || current.startsWith('CAP Grid:')) $('notes').value = defaultNote;
}
function setPointEntryDefaults(lat, lon, heading, capGridLabel){
  if(Number.isFinite(lat) && Number.isFinite(lon)){
    $('latDmm').value = coordToDMM(lat, true);
    $('lonDmm').value = coordToDMM(lon, false);
    $('latDms').value = coordToDMS(lat, true);
    $('lonDms').value = coordToDMS(lon, false);
  }
  if(Number.isFinite(heading)) $('bearing').value = fmtNum(degNorm(heading), 1);
  updateNotesWithCapGrid(capGridLabel);
}
async function lookupCapGridLabelOnly(p){
  if(!p || !capGridLayer || !capSubGridLayer || !EsriPointClass) return '--';
  const pt = new EsriPointClass({longitude:p.lon, latitude:p.lat, spatialReference:{wkid:4326}});
  const queryLayer = async (layer) => {
    const q = layer.createQuery();
    q.geometry = pt;
    q.spatialRelationship = 'intersects';
    q.returnGeometry = false;
    q.outFields = ['CONVENTION','CELL','SUFFIX'];
    q.num = 1;
    return layer.queryFeatures(q);
  };
  try{
    let response = await queryLayer(capSubGridLayer);
    let feature = response.features && response.features[0];
    if(!feature){
      response = await queryLayer(capGridLayer);
      feature = response.features && response.features[0];
    }
    return feature ? capLabelFromAttributes(feature.attributes) : '--';
  }catch(err){
    console.warn('Point-entry CAP Grid lookup failed', err);
    return '--';
  }
}
async function populatePointEntryDefaults(){
  const method = $('method').value;
  if(method !== 'C' && method !== 'D') return;
  if(live && Number.isFinite(live.lat) && Number.isFinite(live.lon)){
    const label = liveCapGridLabel && liveCapGridLabel !== '--' ? liveCapGridLabel : await lookupCapGridLabelOnly(live);
    setPointEntryDefaults(live.lat, live.lon, live.heading, label);
    return;
  }
  if(!navigator.geolocation){
    updateNotesWithCapGrid('GPS unavailable');
    return;
  }
  enableCompass();
  $('notes').value = $('notes').value.trim() || 'CAP Grid: GPS permission/fix pending';
  navigator.geolocation.getCurrentPosition(async pos=>{
    const lat = pos.coords.latitude;
    const lon = pos.coords.longitude;
    const heading = currentHeadingFromPosition(pos);
    const label = await lookupCapGridLabelOnly({lat, lon});
    setPointEntryDefaults(lat, lon, heading, label);
  }, err=>{
    updateNotesWithCapGrid('GPS fix unavailable');
    console.warn('Point-entry GPS default failed', err);
  }, {enableHighAccuracy:true, maximumAge:10000, timeout:20000});
}

function capPointKey(p){
  if(!p || !Number.isFinite(p.lat) || !Number.isFinite(p.lon)) return '';
  // About 35 feet at the equator. This avoids re-querying while GPS jitters.
  return `${p.lat.toFixed(4)},${p.lon.toFixed(4)}`;
}
function capLabelFromAttributes(attrs){
  if(!attrs) return '--';
  const convention = attrs.CONVENTION || attrs.convention || attrs.Convention || '';
  const suffix = attrs.SUFFIX || attrs.suffix || '';
  const cell = attrs.CELL || attrs.cell || '';
  if(convention && suffix && !String(convention).endsWith(String(suffix))) return `${convention}${suffix}`;
  return convention || cell || '--';
}
function makeCapHighlightSymbol(kind){
  const isElt = kind === 'elt';
  return {
    type:'simple-fill',
    color: isElt ? [255,59,48,.16] : [65,209,125,.16],
    outline:{color: isElt ? [255,59,48,1] : [65,209,125,1], width:3}
  };
}
function setCapGridUi(){
  $('eltCapGrid').innerHTML = `CAP Grid: <span class="capgrid-value">${eltCapGridLabel || '--'}</span>`;
  $('liveCapGrid').innerHTML = `CAP Grid: <span class="capgrid-value">${liveCapGridLabel || '--'}</span>`;
}
function clearCapHighlight(kind){
  if(!capHighlightLayer) return;
  const g = kind === 'elt' ? eltHighlightGraphic : liveHighlightGraphic;
  if(g) capHighlightLayer.remove(g);
  if(kind === 'elt') eltHighlightGraphic = null; else liveHighlightGraphic = null;
}
async function queryCapGridForPoint(p, kind){
  if(!p || !capGridLayer || !capSubGridLayer || !EsriPointClass || !EsriGraphicClass) return;
  const key = capPointKey(p);
  if(kind === 'elt' && key === lastEltCapGridKey) return;
  if(kind === 'live' && key === lastLiveCapGridKey) return;
  if(kind === 'elt') lastEltCapGridKey = key; else lastLiveCapGridKey = key;
  const pt = new EsriPointClass({longitude:p.lon, latitude:p.lat, spatialReference:{wkid:4326}});
  const queryLayer = async (layer) => {
    const q = layer.createQuery();
    q.geometry = pt;
    q.spatialRelationship = 'intersects';
    q.returnGeometry = true;
    q.outFields = ['CONVENTION','CELL','SUFFIX'];
    q.num = 1;
    return layer.queryFeatures(q);
  };
  try{
    let response = await queryLayer(capSubGridLayer);
    let feature = response.features && response.features[0];
    if(!feature){
      response = await queryLayer(capGridLayer);
      feature = response.features && response.features[0];
    }
    const label = feature ? capLabelFromAttributes(feature.attributes) : '--';
    if(kind === 'elt') eltCapGridLabel = label; else liveCapGridLabel = label;
    clearCapHighlight(kind);
    if(feature && feature.geometry && capHighlightLayer){
      const highlight = new EsriGraphicClass({geometry:feature.geometry, symbol:makeCapHighlightSymbol(kind)});
      capHighlightLayer.add(highlight);
      if(kind === 'elt') eltHighlightGraphic = highlight; else liveHighlightGraphic = highlight;
    }
    setCapGridUi();
  }catch(err){
    console.warn('CAP Grid lookup failed', err);
    if(kind === 'elt') eltCapGridLabel = 'Lookup failed'; else liveCapGridLabel = 'Lookup failed';
    setCapGridUi();
  }
}
function updateCapGridLookups(){
  if(elt) queryCapGridForPoint(elt, 'elt');
  else { eltCapGridLabel = '--'; lastEltCapGridKey=''; clearCapHighlight('elt'); }
  if(live) queryCapGridForPoint(live, 'live');
  else { liveCapGridLabel = '--'; lastLiveCapGridKey=''; clearCapHighlight('live'); }
  setCapGridUi();
}
function setLiveButton(){ $('toggleLive').textContent = liveWatch ? 'Stop Live Tracking' : 'Start Live Tracking'; }
function updateCards(result){ if(elt){ $('eltStatus').textContent=`${elt.method}: ${elt.quality}`; $('eltStatus').className='big '+(elt.quality.startsWith('Good')?'ok':elt.quality.startsWith('Fair')?'warn':'bad'); $('eltDmm').textContent=`DMM: ${coordToDMM(elt.lat,true)}, ${coordToDMM(elt.lon,false)}`; $('eltDms').textContent=`DMS: ${coordToDMS(elt.lat,true)}, ${coordToDMS(elt.lon,false)}`; $('qualityInfo').textContent=`Method=${elt.method}; Lines=${elt.linesUsed}; RMS=${fmtNum(elt.rms,0)} ft; Radius~${fmtNum(elt.radius,0)} ft; Min angle=${fmtNum(elt.minAngle,1)} deg`; } else { $('eltStatus').textContent='Not enough geometry'; $('eltStatus').className='big warn'; $('eltDmm').textContent='DMM: --'; $('eltDms').textContent='DMS: --'; $('qualityInfo').textContent=result.quality; }
  setLiveButton();
  if(live){ $('liveStatus').textContent=liveWatch?'Tracking':'Last GPS fix'; $('liveStatus').className='big ok'; $('liveDmm').textContent=`DMM: ${coordToDMM(live.lat,true)}, ${coordToDMM(live.lon,false)}`; $('liveDms').textContent=`DMS: ${coordToDMS(live.lat,true)}, ${coordToDMS(live.lon,false)}${Number.isFinite(live.heading)?' | Hdg '+fmtNum(live.heading,0)+'° '+(live.headingSource||''):''}`; }
  else if(liveWatch){ $('liveStatus').textContent='Waiting for GPS fix'; $('liveStatus').className='big warn'; $('liveDmm').textContent='DMM: permission/fix pending'; $('liveDms').textContent=liveError?`GPS: ${liveError}`:'DMS: --'; }
  else { $('liveStatus').textContent=liveError?'GPS not tracking':'Not tracking'; $('liveStatus').className='big muted'; $('liveDmm').textContent=liveError?`GPS: ${liveError}`:'DMM: --'; $('liveDms').textContent='DMS: --'; }
  if(elt && live){ const nb=distanceBearing(live,elt); $('navInfo').textContent=`${fmtNum(nb.bearing,0)}° / ${fmtNum(nb.distanceFt,0)} ft`; let rel=null; if(Number.isFinite(live.heading)){ rel=((nb.bearing-live.heading+540)%360)-180; $('turnInfo').textContent=Math.abs(rel)<5?`On path, error ${fmtNum(rel,1)}°`:`Turn ${Math.abs(rel).toFixed(1)}° ${rel>0?'right':'left'}`; $('needle').style.transform=`rotate(${rel}deg)`; } else { $('turnInfo').textContent='ADF waiting for live heading; move 25+ ft or enable device compass.'; $('needle').style.transform=`rotate(0deg)`; } } else { $('navInfo').textContent='--'; $('turnInfo').textContent=liveWatch && !live?'Turn: waiting for live GPS fix.':'Turn: --'; $('needle').style.transform='rotate(0deg)'; }
}
function updateAll(){ const result=computeElt(); renderTable(); updateCards(result); drawMap(result); updateCapGridLookups(); if(elt && !liveWatch) startLiveTracking(false); }

function csvExport(){ const rows=[['RecordType','Timestamp','Latitude_DMM','Longitude_DMM','Latitude_DMS','Longitude_DMS','Latitude_DD','Longitude_DD','Bearing_To_ELT','ELT_Bearing_Used','Method','Notes']]; for(const p of points){ rows.push([typeName(p.type),p.time,coordToDMM(p.lat,true),coordToDMM(p.lon,false),coordToDMS(p.lat,true),coordToDMS(p.lon,false),p.lat,p.lon,p.bearing,p.bearing,p.type,p.notes||'']); } if(elt) rows.push(['Estimated ELT',nowIso(),coordToDMM(elt.lat,true),coordToDMM(elt.lon,false),coordToDMS(elt.lat,true),coordToDMS(elt.lon,false),elt.lat,elt.lon,'','',elt.method,`Quality=${elt.quality}; RMS_ft=${elt.rms}; Radius_ft=${elt.radius}; Lines=${elt.linesUsed}`]); if(live) rows.push(['Live Team/Aircraft',nowIso(),coordToDMM(live.lat,true),coordToDMM(live.lon,false),coordToDMS(live.lat,true),coordToDMS(live.lon,false),live.lat,live.lon,Number.isFinite(live.heading)?live.heading:'','','T','Current live tracking point; headingSource='+(live.headingSource||'none')]); const blob=new Blob([rows.map(r=>r.map(escapeCsv).join(',')).join('\n')],{type:'text/csv'}); const dl=document.createElement('a'); dl.href=URL.createObjectURL(blob); dl.download='elt_finder_log_'+new Date().toISOString().replace(/[:.]/g,'')+'.csv'; dl.click(); URL.revokeObjectURL(dl.href); }
async function startLiveTracking(userRequested=true){
  if(userRequested) enableCompass();
  if(!navigator.geolocation){ liveError='Geolocation is not available in this browser.'; updateAll(); if(userRequested) alert(liveError); return; }
  if(liveWatch) return;
  liveError=''; setLiveButton(); $('liveStatus').textContent='Waiting for GPS fix'; $('liveStatus').className='big warn'; $('liveDmm').textContent='DMM: permission/fix pending'; $('liveDms').textContent='DMS: --';
  liveWatch=navigator.geolocation.watchPosition(pos=>{
    const t=Date.now();
    if(t-lastLiveUpdate<30000 && live) return;
    lastLiveUpdate=t; liveError='';
    const h = bestLiveHeading(pos);
    live={type:'T', lat:pos.coords.latitude, lon:pos.coords.longitude, heading:h.heading, headingSource:h.source, time:nowIso()};
    updateAll();
  }, err=>{
    liveError=err && err.message ? err.message : 'Unable to acquire GPS fix.';
    if(liveWatch){ navigator.geolocation.clearWatch(liveWatch); liveWatch=null; }
    setLiveButton(); updateAll();
    if(userRequested) alert('GPS error: '+liveError);
  }, {enableHighAccuracy:true, maximumAge:10000, timeout:20000});
  setLiveButton();
}
function stopLiveTracking(){ if(liveWatch){navigator.geolocation.clearWatch(liveWatch); liveWatch=null;} setLiveButton(); updateAll(); }
function recordGpsOnce(){ if(!navigator.geolocation){alert('Geolocation is not available.'); return;} navigator.geolocation.getCurrentPosition(pos=>{ $('latDmm').value=coordToDMM(pos.coords.latitude,true); $('lonDmm').value=coordToDMM(pos.coords.longitude,false); syncFromDmm(); addPoint(pos.coords.latitude,pos.coords.longitude); }, err=>alert('GPS error: '+err.message), {enableHighAccuracy:true, maximumAge:5000, timeout:20000}); }

require(['esri/Map','esri/views/MapView','esri/layers/GraphicsLayer','esri/layers/FeatureLayer','esri/Graphic','esri/geometry/Point','esri/geometry/Polyline','esri/geometry/Circle','esri/geometry/Extent','esri/geometry/support/webMercatorUtils'], function(Map,MapView,GraphicsLayer,FeatureLayer,Graphic,Point,Polyline,Circle,Extent,webMercatorUtils){
  
  EsriPointClass = Point;
  EsriGraphicClass = Graphic;
  graphicsLayer=new GraphicsLayer({title:'ELT Finder Graphics'});
  capHighlightLayer=new GraphicsLayer({title:'Selected CAP Grid Cells'});
  capGridLayer=new FeatureLayer({
    url: CAP_GRID_SERVICE + '/0',
    title:'CAP SAR Grids',
    visible:true,
    outFields:['CONVENTION','CELL'],
    popupTemplate:{title:'CAP Grid {CONVENTION}', content:'Cell: {CELL}'},
    labelingInfo:[{
      labelExpressionInfo:{expression:'$feature.CONVENTION'},
      labelPlacement:'always-horizontal',
      minScale:1000000,
      symbol:{type:'text', color:'black', haloColor:'white', haloSize:1, font:{size:12, weight:'bold'}}
    }]
  });
  capSubGridLayer=new FeatureLayer({
    url: CAP_GRID_SERVICE + '/1',
    title:'CAP SAR Subgrids',
    visible:true,
    outFields:['CONVENTION','CELL','SUFFIX'],
    popupTemplate:{title:'CAP Subgrid {CONVENTION}', content:'Cell: {CELL}<br>Suffix: {SUFFIX}'},
    renderer:{type:'simple', symbol:{type:'simple-fill', color:[252,224,191,0], outline:{color:[0,0,0,.75], width:.75, style:'dash'}}},
    labelingInfo:[{
      labelExpressionInfo:{expression:'$feature.SUFFIX'},
      labelPlacement:'always-horizontal',
      minScale:500000,
      symbol:{type:'text', color:'black', haloColor:'white', haloSize:1, font:{size:10, weight:'bold'}}
    }]
  });
  const map=new Map({basemap:'hybrid', layers:[capGridLayer, capSubGridLayer, capHighlightLayer, graphicsLayer]});
  view=new MapView({container:'viewDiv', map, center:[-79,33.68], zoom:10, constraints:{snapToZoom:false}});
  try{ countyLayer=new FeatureLayer({url:'https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Counties_Generalized_Boundaries/FeatureServer/0', title:'Counties', opacity:.85, outFields:['NAME','STATE_NAME'], labelingInfo:[{labelExpressionInfo:{expression:'$feature.NAME'}, symbol:{type:'text',color:'white',haloColor:'black',haloSize:1,font:{size:10,weight:'bold'}}}], renderer:{type:'simple',symbol:{type:'simple-fill',color:[0,0,0,0],outline:{color:[255,255,255,.55],width:1}}}}); map.add(countyLayer, 0); }catch(e){console.warn(e);}
  view.on('click', ev=>{ if(!$('mapClick').checked) return; const mp=view.toMap({x:ev.x,y:ev.y}); if(mp) addPoint(mp.latitude, mp.longitude); });
  window.__drawMap = function(result){ graphicsLayer.removeAll(); const extPts=[]; const ref=result.ref; const addG=(g)=>graphicsLayer.add(g); const pt=(lon,lat)=>new Point({longitude:lon,latitude:lat}); const marker=(color,shape='circle',size=12)=>({type:'simple-marker',style:shape,color, size, outline:{color:[0,0,0,.9],width:1}}); const text=(txt,color,dy=-18)=>({type:'text',text:txt,color,haloColor:'black',haloSize:1.4,yoffset:dy,font:{size:14,weight:'bold'}}); const lineSym=(color,width=2,dash=null)=>({type:'simple-line',color,width,style:dash||'solid'});
    for(const p of points){ extPts.push([p.lon,p.lat]); const color=p.type==='C'?[47,140,255,1]:[255,210,63,1]; addG(new Graphic({geometry:pt(p.lon,p.lat), symbol:marker(color,p.type==='C'?'circle':'diamond'), popupTemplate:{title:typeName(p.type),content:`${coordToDMM(p.lat,true)}, ${coordToDMM(p.lon,false)}<br>${p.time}`}})); addG(new Graphic({geometry:pt(p.lon,p.lat), symbol:text(p.type, p.type==='C'?'#2f8cff':'#ffd23f')})); }
    const cps=points.filter(p=>p.type==='C'); for(let i=0;i<cps.length;i++) for(let j=i+1;j<cps.length;j++){ addG(new Graphic({geometry:new Polyline({paths:[[[cps[i].lon,cps[i].lat],[cps[j].lon,cps[j].lat]]], spatialReference:{wkid:4326}}), symbol:lineSym([47,140,255,1],2)})); }
    for(const l of result.lines){ const scale=elt?Math.max(5280,distanceBearing(unproject(l.p,ref),elt).distanceFt*1.3):20000; const a=unproject({x:l.p.x-l.v.x*scale,y:l.p.y-l.v.y*scale},ref), b=unproject({x:l.p.x+l.v.x*scale,y:l.p.y+l.v.y*scale},ref); addG(new Graphic({geometry:new Polyline({paths:[[[a.lon,a.lat],[b.lon,b.lat]]], spatialReference:{wkid:4326}}), symbol:lineSym([255,59,48,.9],2,l.kind.includes('bisector')?'dash':'solid')})); extPts.push([a.lon,a.lat],[b.lon,b.lat]); }
    if(elt){ extPts.push([elt.lon,elt.lat]); addG(new Graphic({geometry:pt(elt.lon,elt.lat), symbol:marker([255,59,48,1],'x',18)})); addG(new Graphic({geometry:pt(elt.lon,elt.lat), symbol:text('E','#ff3b30',-22)})); if(elt.radius){ addG(new Graphic({geometry:new Circle({center:pt(elt.lon,elt.lat), radius:elt.radius, radiusUnit:'feet'}), symbol:{type:'simple-fill',color:[255,59,48,.08],outline:{color:[255,59,48,.85],width:2}}})); } }
    if(live){ extPts.push([live.lon,live.lat]); addG(new Graphic({geometry:pt(live.lon,live.lat), symbol:marker([65,209,125,1],'triangle',16)})); addG(new Graphic({geometry:pt(live.lon,live.lat), symbol:text('T','#41d17d',-22)})); if(elt){ addG(new Graphic({geometry:new Polyline({paths:[[[live.lon,live.lat],[elt.lon,elt.lat]]], spatialReference:{wkid:4326}}), symbol:lineSym([65,209,125,1],3)})); } }
  };
  window.__goFull = function(){ const pts=[...points, ...(elt?[elt]:[]), ...(live?[live]:[])]; if(!pts.length) return; let xmin=Math.min(...pts.map(p=>p.lon)), xmax=Math.max(...pts.map(p=>p.lon)), ymin=Math.min(...pts.map(p=>p.lat)), ymax=Math.max(...pts.map(p=>p.lat)); const pad=.02; view.goTo(new Extent({xmin:xmin-pad,xmax:xmax+pad,ymin:ymin-pad,ymax:ymax+pad,spatialReference:{wkid:4326}})).catch(()=>{}); };
  updateAll();
});
function drawMap(result){ if(window.__drawMap) window.__drawMap(result); }

$('latDmm').addEventListener('change',syncFromDmm); $('lonDmm').addEventListener('change',syncFromDmm); $('latDms').addEventListener('change',syncFromDms); $('lonDms').addEventListener('change',syncFromDms); $('method').addEventListener('change',()=>updateMethodUi(true));
$('addPoint').onclick=addPointFromForm; $('clearForm').onclick=clearEntry; $('clearAll').onclick=()=>{ if(confirm('Clear all recorded points and ELT estimate?')){points=[];elt=null;updateAll();} }; $('exportLog').onclick=csvExport; $('gpsOnce').onclick=recordGpsOnce; $('toggleLive').onclick=()=> liveWatch?stopLiveTracking():startLiveTracking(true); $('centerElt').onclick=()=>{if(elt&&view)view.goTo({center:[elt.lon,elt.lat],zoom:15});}; $('centerLive').onclick=()=>{if(live&&view)view.goTo({center:[live.lon,live.lat],zoom:15});}; $('fullExtent').onclick=()=>window.__goFull&&window.__goFull(); $('toggleCapGrid').onclick=()=>{capGridVisible=!capGridVisible; $('toggleCapGrid').textContent=capGridVisible?'CAP Grid On':'CAP Grid Off'; if(capGridLayer) capGridLayer.visible=capGridVisible; if(capSubGridLayer) capSubGridLayer.visible=capGridVisible; if(capHighlightLayer) capHighlightLayer.visible=capGridVisible;};
updateMethodUi(false);

})();
