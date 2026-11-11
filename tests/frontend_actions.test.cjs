const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('monitor.html', 'utf8');

// Execute the actual function bodies with only browser/network boundaries replaced.
function load(ctx, ...names) {
  vm.createContext(ctx);
  for (const name of names) {
    const re = new RegExp('(?:async )?function '+name+'\\(');
    const start = html.search(re);
    assert.ok(start >= 0, name);
    let pos = html.indexOf('{', start), depth = 0;
    do {
      if (html[pos] === '{') depth++;
      if (html[pos] === '}') depth--;
      pos++;
    } while (depth > 0 && pos < html.length);
    vm.runInContext(html.slice(start, pos), ctx);
  }
  return ctx;
}
const plain = value => JSON.parse(JSON.stringify(value));

test('saving silence alone persists settings without overwriting any secret', async()=>{
  const writes = [], secrets = [];
  const silence = {enabled:true,start:'23:00',end:'08:00'};
  const ctx = load({window:{}, document:{getElementById:()=>({style:{}})},
    getGhToken:()=> 'test-token', buildSilenceConfig:()=>silence, buildPushConfig:()=>null,
    buildTemplateConfig:()=>({}), renderSilenceHint:()=>{}, toast:()=>{},
    ghWriteWithRetry:async(path,mutate)=>{writes.push({path,value:plain(mutate({}).rooms)});},
    _encryptSaveBlive:async(cfg)=>{secrets.push(cfg);return true;}
  }, 'saveSilenceConfig');
  await ctx.saveSilenceConfig();
  assert.deepEqual(writes,[{path:'silence_state.json',value:silence}]);
  assert.deepEqual(secrets,[]);
});

function writer(initial, latest, failures = 1) {
  let reads = 0;
  const writes = [];
  const ctx = load({Promise, Object, JSON,
    setTimeout:fn=>fn(),
    ghGetFile:async()=>({rooms:plain(reads++ ? latest : initial), sha:'sha-'+reads}),
    ghPutFile:async(path,payload,sha)=>{
      writes.push({path,payload:plain(payload),sha});
      if(writes.length<=failures) throw Object.assign(Error('HTTP 409'),{conflict:true});
      return 'saved';
    }
  },'enhancedMerge','isRetryableError','ghWriteWithRetry');
  return {ctx,writes};
}

test('409 retry keeps deletion and concurrent additions using the latest SHA',async()=>{
  const {ctx,writes}=writer([{id:'A'},{id:'B'}],[{id:'A'},{id:'B'},{id:'C'}]);
  const out=await ctx.ghWriteWithRetry('rooms.json',rows=>({rooms:rows.filter(x=>x.id!=='A'),changed:true}));
  assert.deepEqual(plain(out.rooms),[{id:'B'},{id:'C'}]);
  assert.equal(writes[1].sha,'sha-2');
});

test('409 retry preserves an intentional rename and unrelated remote fields',async()=>{
  const {ctx}=writer([{id:'A',name:'old'}],[{id:'A',name:'old',sec_uid:'new-uid'}]);
  const out=await ctx.ghWriteWithRetry('rooms.json',rows=>({rooms:rows.map(r=>({...r,name:'chosen'})),changed:true}));
  assert.deepEqual(plain(out.rooms),[{id:'A',name:'chosen',sec_uid:'new-uid'}]);
});

test('409 retry supports object settings and preserves concurrent receipt',async()=>{
  const {ctx}=writer({enabled:false,lastSent:10},{enabled:false,lastSent:20});
  const out=await ctx.ghWriteWithRetry('summary_state.json',state=>({rooms:{...state,enabled:true},changed:true}));
  assert.deepEqual(plain(out.rooms),{enabled:true,lastSent:20});
});

test('conflict exhaustion rejects instead of reporting a successful save',async()=>{
  const {ctx,writes}=writer([],[],10);
  await assert.rejects(ctx.ghWriteWithRetry('rooms.json',()=>({rooms:[{id:'A'}],changed:true})),/409/);
  assert.equal(writes.length,6);
});

function summaryPage(remote, fail=false) {
  const notices=[], writes=[], secrets=[];
  const ctx=load({window:{__summaryState:{enabled:true,lastSent:1}}, Date, Math, Object,
    document:{getElementById:()=>({style:{}})}, getGhToken:()=> 'test-token',
    renderSummary:()=>{}, toast:(...x)=>notices.push(x),
    computeSince:()=>100, bjNow:()=>new Date(),
    buildSummaryConfig:()=>({enabled:true,freq:'weekly',sendTime:'10:00'}),
    buildPushConfig:()=>({push:{type:'wecom',webhook:'test'}}),
    fetch:async()=>{secrets.push('request');throw Error('unexpected secret access');},
    ghWriteWithRetry:async(path,mutate)=>{
      const result=mutate(plain(remote));
      if(fail) throw Error('HTTP 403');
      remote=plain(result.rooms); writes.push({path,changed:result.changed,value:remote});
      return result;
    }
  },'requestPushSummary','saveSummaryConfig');
  return {ctx,writes,notices,secrets};
}

test('manual summary creates a pending request, preserves receipt, coalesces double click',async()=>{
  const {ctx,writes}=summaryPage({enabled:true,freq:'daily',lastSent:200,lastFailedAt:50});
  await ctx.requestPushSummary();
  const first=writes[0].value;
  assert.equal(first.lastSent,200);
  assert.ok(first.requestId);
  assert.equal(first.lastFailedAt,50);
  await ctx.requestPushSummary();
  assert.equal(writes[1].value.requestId,first.requestId);
  assert.equal(writes[1].changed,false);
});

test('failed request does not mark browser state sent or requested',async()=>{
  const {ctx}=summaryPage({enabled:true,lastSent:200},true);
  const before=plain(ctx.window.__summaryState);
  await ctx.requestPushSummary();
  assert.deepEqual(plain(ctx.window.__summaryState),before);
});

test('summary settings preserve latest receipts and requests without rewriting secrets',async()=>{
  const {ctx,writes,secrets}=summaryPage({enabled:false,freq:'daily',lastSent:200,requestId:'pending',lastHandledRequestId:'old'});
  await ctx.saveSummaryConfig();
  assert.deepEqual(writes[0].value,{enabled:true,freq:'weekly',sendTime:'10:00',lastSent:200,requestId:'pending',lastHandledRequestId:'old'});
  assert.deepEqual(secrets,[]);
});
