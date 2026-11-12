const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../monitor.html'), 'utf8');

function setup(hist, rooms = []) {
  const el = {innerHTML:''};
  const ctx = {hist, stat:{rooms}, Date, Math, Object,
    bjNow:()=>new Date('2026-11-12T23:00:00Z'),
    parseBeijing:s=>s ? Date.parse(s.replace(' ', 'T') + 'Z') : null,
    roomKeyOf:r=>r.platform + '|' + (r.rid || r.id),
    fmtDuration:n=>`${n}s`, e:s=>s,
    document:{getElementById:()=>el}};
  vm.createContext(ctx);
  vm.runInContext(html.slice(html.indexOf('function computeLiveDuration('),
    html.indexOf('/* ---------- C2 更长趋势')), ctx);
  return {ctx,el};
}
function observation(time, seconds, ended=false) {
  return {platform:'bilibili',rid:'123',time,type:ended?'live_off':'live_on',
    duration_session:{start:'2026-11-12 12:00:00',last_seen:'2026-11-12 13:00:00',seconds,ended}};
}

test('repeated observations count one session and use last confirmed duration',()=>{
  const {ctx} = setup([observation('2026-11-12 12:05:00',300),
    observation('2026-11-12 13:00:00',3600),observation('2026-11-12 22:00:00',3600,true)]);
  const d=ctx.computeLiveDuration(ctx.hist,'bilibili|123');
  assert.equal(d.totalSec,3600);
  assert.equal(d.sessionCount,1);
  assert.equal(d.completedCount,1);
});

test('ongoing duration is bounded by observation, never wall clock',()=>{
  const {ctx} = setup([observation('2026-11-12 13:00:00',3600)]);
  assert.equal(ctx.computeLiveDuration(ctx.hist,'bilibili|123').totalSec,3600);
});

test('unknown continuity is excluded and visibly labelled',()=>{
  const {ctx,el} = setup([observation('2026-11-12 13:00:00',3600),
    observation('2026-11-12 22:00:00',null,true)]);
  const d=ctx.computeLiveDuration(ctx.hist,'bilibili|123');
  assert.equal(d.totalSec,0);
  assert.equal(d.completedCount,0);
  assert.equal(d.unknownCount,1);
  ctx.renderDurationCard();
  assert.match(el.innerHTML,/未知|证据不足/);
});

test('status snapshot covers a long session whose start log was trimmed',()=>{
  const latest = observation('2026-11-13 13:00:00',90000);
  const {ctx} = setup([], [latest]);
  const d=ctx.computeLiveDurationAll([]);
  assert.equal(d.totalSec,90000);
  assert.equal(d.sessionCount,1);
});

test('legacy paired events are not presented as verified duration',()=>{
  const {ctx,el} = setup([{platform:'bilibili',rid:'123',type:'live_on',time:'2026-11-12 12:00:00'},
    {platform:'bilibili',rid:'123',type:'live_off',time:'2026-11-12 22:00:00'}]);
  assert.equal(ctx.computeLiveDuration(ctx.hist,'bilibili|123').totalSec,0);
  ctx.renderDurationCard();
  assert.match(el.innerHTML,/未知|证据不足/);
});
