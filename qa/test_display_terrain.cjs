const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../gui-v2/frontend/node_modules/typescript');
const compiled = ts.transpileModule(fs.readFileSync('gui-v2/frontend/src/lib/map/terrain.ts','utf8'), {
  compilerOptions: {module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}
}).outputText;
const context = {exports:{},AbortController,Float32Array,Map,Promise,Math,Array,setTimeout,clearTimeout};
vm.runInNewContext(compiled,context);
const {TerrainTiles,TERRAIN_GRID,containsTerrainPoint,decodeTerrainPixel}=context.exports;
function tile(height,encoding,valid=true) {
  const data = new Uint8ClampedArray(256*256*4);
  const value = encoding==='terrarium' ? Math.round((height+32768)*256) : Math.round((height+10000)*10);
  for(let i=0;i<data.length;i+=4){data[i]=value>>16;data[i+1]=(value>>8)&255;data[i+2]=value&255;data[i+3]=valid?255:0;}
  return {data,width:256,height:256};
}
(async()=>{
  assert.equal(decodeTerrainPixel(tile(-32.25,'terrarium'),3,9,'terrarium'),-32.25);
  assert.equal(decodeTerrainPixel(tile(120,'mapbox',false),3,9,'mapbox'),null);
  assert(containsTerrainPoint([170,-10,-170,10],179,0));
  assert(containsTerrainPoint([170,-10,-170,10],-179,0));
  assert(!containsTerrainPoint([170,-10,-170,10],0,0));
  const tiles = new TerrainTiles(()=>{}), seen=[];
  tiles.get=async(source,z,x,y)=>{
    seen.push({source:source.id,z,x,y});
    // The middle quarter of the fetched DEM is a retained gap.
    const result=tile(source.id==='background'?100:source.id==='low'?200:300,source.encoding);
    if(source.id==='high')for(let row=0;row<256;row++)for(let col=64;col<128;col++)result.data[(row*256+col)*4+3]=0;
    return result;
  };
  const base = await tiles.heightmap(1,1,2,[]);
  assert(base.every(h=>h===100));
  const overlays=[{id:'low',template:'low',bounds:[-80,10,-10,60]},{id:'high',template:'high',bounds:[-80,10,-10,60]}];
  const mixed = await tiles.heightmap(1,1,2,overlays);
  assert(mixed.includes(100),'background survives outside acquired coverage');
  assert(mixed.includes(200),'gap reveals next acquired DEM');
  assert(mixed.includes(300),'last stable overlay wins');
  const left = await tiles.heightmap(1,1,2,overlays),right=await tiles.heightmap(2,1,2,overlays);
  for(let row=0;row<TERRAIN_GRID;row++)assert.equal(left[row*TERRAIN_GRID+TERRAIN_GRID-1],right[row*TERRAIN_GRID]);
  await tiles.heightmap(3000,3000,16,[]);
  assert(seen.filter(x=>x.source==='background').every(x=>x.z<=12),'background zoom is bounded');
  tiles.dispose();
  const before=seen.length; await tiles.heightmap(1,1,2,overlays);assert.equal(seen.length,before,'disposed provider stops requesting');
  let running=0,peak=0,warnings=0;
  context.fetch=async()=>{running++;peak=Math.max(peak,running);await new Promise(r=>setTimeout(r,5));running--;return {ok:false,status:503};};
  const failed=new TerrainTiles(()=>warnings++);
  const unavailable=await Promise.all(Array.from({length:30},(_,i)=>failed.download('fixture-'+i)));
  assert(unavailable.every(x=>x===null),'failed sources permit display fallback');
  assert(peak<=8,'download concurrency remains bounded');assert.equal(warnings,1,'one visible notice per provider');
  failed.dispose();
  const samplerCode=ts.transpileModule(fs.readFileSync('gui-v2/frontend/src/lib/terrainSampler.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  const samplerContext={exports:{}};vm.runInNewContext(samplerCode,samplerContext);
  const sampler=new samplerContext.exports.TerrainSampler('fixture');sampler.getTile=async()=>tile(100,'mapbox',false);
  assert.equal(await sampler.sample(0,0,8),null,'measurements do not treat NoData as an elevation');
  console.log('Terrain decoding, dateline, coverage, gaps, precedence, shared edges, zoom cap, disposal, outage, concurrency and measurement checks passed');
})().catch(e=>{console.error(e);process.exitCode=1});
