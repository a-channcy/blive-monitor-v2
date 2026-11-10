const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('monitor.html', 'utf8');

function setup({failed = [], malformed = {}, age = 0} = {}) {
  const elements = {};
  const calls = [];
  const updated = new Date(Date.now() + 8*3600000 - age*60000).toISOString().slice(0,19).replace('T',' ');
  const fixtures = {'status.json':{updated, rooms:[]}, 'rooms.json':[{id:'123',name:'A'}],
    'history.json':[], 'post_rooms.json':[], 'post_tracking.json':{},
    'summary_state.json':{enabled:true}, 'silence_state.json':{enabled:true}};
  const ctx = {console, Date, Promise, AbortSignal, URL, Array, Object,
    GH_REPO:'a-channcy/blive-monitor-v2', GH_BRANCH:'main',
    stat:{updated:'2026-01-01 00:00:00',rooms:[]}, rooms:[], hist:[], postRooms:[], postTrack:{},
    FRESH_WARN_MIN:10,FRESH_STALE_MIN:30, window:{},
    document:{getElementById:id=>(elements[id] ||= {textContent:'',innerHTML:'',style:{},dataset:{}})},
    getGhToken:()=>'', e:s=>s, renderHealthBar:f=>{ctx.health=f;},
    apiGetRooms:async()=>fixtures['rooms.json'], apiGetPostRooms:async()=>fixtures['post_rooms.json'],
    fetch:async(url,opts)=>{
      calls.push({url,opts});
      const name = new URL(url, 'https://pages.example/').pathname.split('/').pop();
      if(failed.includes(name)) throw Error('offline');
      return {ok:true,json:async()=>name in malformed ? malformed[name] : fixtures[name]};
    }
  };
  for(const name of ['readViewParam','renderLive','renderPosts','renderLog','renderDashboard','maybeShowSummary','loadPhase2UI']) ctx[name]=()=>{};
  vm.createContext(ctx);
  const helperStart = html.indexOf('function fetchPublicData(');
  if(helperStart >= 0) vm.runInContext(html.slice(helperStart,html.indexOf('function ld(){',helperStart)),ctx);
  vm.runInContext(html.slice(html.indexOf('function parseBeijing('),html.indexOf('function computeStatsJS(')),ctx);
  vm.runInContext(html.slice(html.indexOf('function ld(){'),html.indexOf('/* ===== Token 设置',html.indexOf('function ld(){'))),ctx);
  return {ctx,elements,calls,updated};
}
async function load(ctx){ await ctx.ld(); await new Promise(r=>setImmediate(r)); }

test('anonymous reader loads live repository data, never the Pages snapshot',async()=>{
  const {ctx,calls,updated}=setup(); await load(ctx);
  assert.equal(ctx.rooms.length,1);
  assert.equal(ctx.stat.updated,updated);
  assert.ok(calls.length>=5);
  for(const {url,opts} of calls){
    assert.equal(new URL(url,'https://pages.example').origin,'https://raw.githubusercontent.com');
    assert.ok(url.includes('/a-channcy/blive-monitor-v2/main/'));
    assert.equal(opts.headers?.Authorization,undefined);
  }
});
test('configuration read failure is visible without erasing saved settings',async()=>{
  const {ctx,elements}=setup({failed:['silence_state.json']});
  assert.equal(typeof ctx.loadConfigMirror,'function');
  assert.equal(await ctx.loadConfigMirror('silence_state.json','silenceStatus'),null);
  assert.match(elements.silenceStatus.textContent,/读取失败/);
  assert.equal(elements.silenceStatus.style.display,'block');
});
test('failed status refresh keeps old data but reports failure, never success',async()=>{
  const {ctx,elements}=setup({failed:['status.json']}); await load(ctx);
  assert.equal(ctx.stat.updated,'2026-01-01 00:00:00');
  assert.match(elements.sdot.innerHTML,/读取失败/);
  assert.doesNotMatch(elements.sdot.innerHTML,/已更新/);
  assert.equal(ctx.health.state,'loadfail');
});
test('successful request for stale data is visibly stale and shows data timestamp',async()=>{
  const {ctx,elements,updated}=setup({age:40}); await load(ctx);
  assert.match(elements.sdot.innerHTML,/过期/);
  assert.ok(elements.stime.textContent.includes(updated));
});
test('malformed status does not replace usable old data',async()=>{
  const {ctx,elements}=setup({malformed:{'status.json':{rooms:'invalid'}}}); await load(ctx);
  assert.equal(ctx.stat.updated,'2026-01-01 00:00:00');
  assert.match(elements.sdot.innerHTML,/读取失败/);
});
test('failed auxiliary read preserves previous data and reports partial refresh',async()=>{
  const {ctx,elements}=setup({failed:['history.json']}); ctx.hist=[{type:'live_on'}]; await load(ctx);
  assert.equal(ctx.hist.length,1);
  assert.match(elements.sdot.innerHTML,/部分数据读取失败/);
});
test('authenticated room edits are read from the API instead of a stale Raw copy',async()=>{
  const {ctx}=setup();
  ctx.getGhToken=()=>'user-token';
  ctx.apiGetRooms=async()=>[{id:'456',name:'Recently added'}];
  await load(ctx);
  assert.equal(ctx.rooms[0].id,'456');
});
test('expired write credentials do not prevent viewing public room data',async()=>{
  const {ctx}=setup();
  ctx.getGhToken=()=>'expired';
  ctx.apiGetRooms=async()=>{throw Error('401');};
  await load(ctx);
  assert.equal(ctx.rooms[0].id,'123');
});
test('authenticated post edits are not overwritten by stale Raw lists',async()=>{
  const {ctx}=setup();
  ctx.getGhToken=()=>'user-token';
  ctx.apiGetRooms=async()=>[{id:'123'}];
  ctx.apiGetPostRooms=async()=>[{id:'new-post-account'}];
  await load(ctx);
  assert.equal(ctx.postRooms.length,1);
  assert.equal(ctx.postRooms[0].id,'new-post-account');
});
