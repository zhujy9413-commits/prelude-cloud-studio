// Event/clock checks for the real waiting UI. No browser or provider requests.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('dist/waiting.js', 'utf8');
class Target {
  constructor() {
    this.events = {}; this.attrs = {}; this.dataset = {}; this.hidden = false; this.textContent = '';
    const items = new Set();
    this.classList = {add: (...a) => a.forEach(x => items.add(x)), remove: (...a) => a.forEach(x => items.delete(x)),
      contains: x => items.has(x), toggle: (x, on) => on ? items.add(x) : items.delete(x)};
  }
  addEventListener(name, fn) { (this.events[name] ??= []).push(fn); }
  emit(name) { for (const fn of this.events[name] || []) fn(); }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return Object.hasOwn(this.attrs, k); }
  closest() { return null; }
}
function fixture({storage = new Map(), reduced = false, blockedStorage = false} = {}) {
  const visual = new Target(), progress = new Target(), clouds = new Target(), player = new Target();
  const doc = new Target(), win = new Target(), query = new Target(), status = new Target();
  const stages = Array.from({length: 5}, () => new Target());
  const nodes = Object.fromEntries(['job-progress-title','job-progress-detail','job-elapsed','job-eyebrow'].map(id => ['#' + id, new Target()]));
  progress.hidden = true; progress.querySelector = s => s === '.waiting-clouds' ? clouds : nodes[s];
  progress.querySelectorAll = () => stages; visual.querySelector = () => progress;
  doc.hidden = false; doc.querySelector = s => s === '#preview-status' ? status : null;
  query.matches = reduced; clouds.pause = () => { clouds.paused = true; };
  clouds.load = () => {}; clouds.play = () => { clouds.paused = false; return Promise.resolve(); };
  player.readyState = 0; player.paused = true; player.ended = false;
  let now = 1_790_000_000_000, sequence = 0;
  const timers = new Map(), intervals = new Map();
  const context = {window: win, document: doc, matchMedia: () => query,
    localStorage: {getItem: k => storage.get(k) ?? null, setItem: (k,v) => { if (blockedStorage) throw Error('blocked'); storage.set(k,v); }},
    Date: class extends Date {static now() {return now;}},
    setTimeout: (fn, delay) => {const id = ++sequence; timers.set(id, {fn, at: now + delay}); return id;},
    clearTimeout: id => timers.delete(id),
    setInterval: fn => {const id = ++sequence; intervals.set(id, fn); return id;}, clearInterval: id => intervals.delete(id),
    IntersectionObserver: class {observe() {}}, MutationObserver: class {observe() {}},
  };
  vm.runInNewContext(source, context);
  const waiting = win.PreludeWaiting.create(visual, player);
  const request = {request_id: 'request-one', created_at: now / 1000};
  const job = {id: 'job-one', request_id: request.request_id, created_at: request.created_at, status: 'RUNNING', playback_url: '/flow.mp4'};
  return {waiting, visual, progress, clouds, player, doc, win, status, stages, nodes, request, job, storage,
    now: () => now, records: () => JSON.parse(storage.get(win.PreludeWaiting.storageKey) || '[]'),
    step(ms) {now += ms; for (const fn of [...intervals.values()]) fn(); for (const [id,t] of [...timers]) if (t.at <= now) {timers.delete(id); t.fn();}},
    complete() {player.setAttribute('src', job.playback_url); waiting.render({...job, status:'SUCCEEDED'});},
  };
}
let f = fixture();
f.waiting.begin(f.request);
for (const state of ['SUBMITTING','PENDING','RUNNING','DOWNLOADING','PREPARING']) {
  f.waiting.render({...f.job, status:state}); f.step(1000);
  assert.equal(f.progress.hidden, false); assert.equal(f.clouds.paused, false);
}
f.complete(); f.step(5000);
assert.match(f.nodes['#job-elapsed'].textContent, /00:10.*正在准备播放/);
assert.equal(f.visual.dataset.scene, 'loading');
assert.equal(f.stages[4].getAttribute('aria-current'), 'step');
f.player.readyState = 2; f.player.emit('loadeddata');
assert.equal(f.visual.dataset.scene, 'loading'); assert.equal(f.records()[0].playable_at, null);
f.step(2000); f.player.readyState = 3; f.player.emit('canplay');
assert.equal(f.visual.dataset.scene, 'revealing'); assert.equal(f.records()[0].playable_at, f.now());
f.step(900); assert.equal(f.progress.hidden, true); assert.match(f.status.textContent, /从提交到可播放 00:12/);
const firstPlayable = f.records()[0].playable_at;
f.step(100000); f.waiting.render({...f.job, status:'SUCCEEDED'}); f.player.emit('canplay');
assert.equal(f.records()[0].playable_at, firstPlayable); assert.equal(f.visual.dataset.scene, 'ready');
f.player.paused = false; f.player.emit('waiting'); assert.match(f.status.textContent, /正在缓冲/);
f.player.emit('playing'); assert.match(f.status.textContent, /从提交到可播放 00:12/);
const persisted = fixture({storage: f.storage}); persisted.step(100000); persisted.complete();
assert.match(persisted.nodes['#job-elapsed'].textContent, /首次可播放用时 00:12/);
persisted.player.readyState = 4; persisted.player.emit('canplay'); persisted.step(900);
assert.equal(persisted.records()[0].playable_at, firstPlayable); assert.match(persisted.status.textContent, /00:12/);

// A historical work has no click timestamp. Do not invent a wait from its age.
f = fixture(); f.complete(); assert.equal(f.nodes['#job-elapsed'].textContent, '成片已保存 · 正在准备播放');
f.player.readyState = 3; f.player.emit('canplay'); f.step(900);
assert.equal(f.records().length, 0); assert.doesNotMatch(f.status.textContent, /从提交/);

// The actual private blob source remains tied to the original owned media endpoint.
f = fixture(); f.complete(); f.player.setAttribute('src','blob:private-film');
f.player.dataset.playbackSource = f.job.playback_url;
f.waiting.transfer({loaded:500000,total:2000000});
assert.match(f.nodes['#job-progress-detail'].textContent,/0.5 \/ 2.0 MB/);
f.player.readyState=3;f.player.emit('canplay');f.step(900);assert.equal(f.visual.dataset.scene,'ready');

// A decodable first fragment is insufficient until the loader's buffer gate passes.
f=fixture();f.waiting.begin(f.request);f.complete();f.player.readyState=4;
f.player.dataset.playbackReady='false';f.player.emit('canplay');
assert.equal(f.visual.dataset.scene,'loading');assert.equal(f.records()[0].playable_at,null);
f.player.dataset.playbackReady='true';f.waiting.playable();f.step(900);
assert.equal(f.visual.dataset.scene,'ready');const originalTiming=f.records()[0].playable_at;
f.waiting.reload();assert.equal(f.visual.dataset.scene,'loading');
f.waiting.playable();f.step(900);assert.equal(f.records()[0].playable_at,originalTiming);

// Late events from a previous source cannot reveal or time a different film.
f = fixture(); f.waiting.begin(f.request); f.complete(); f.player.setAttribute('src', '/old.mp4');
f.player.readyState = 4; f.player.emit('canplay'); assert.equal(f.visual.dataset.scene, 'loading');
f.player.setAttribute('src', f.job.playback_url); f.player.emit('canplay'); f.waiting.clear(); f.step(900);
assert.equal(f.progress.hidden, true); assert.equal(f.visual.dataset.scene, undefined);
f.player.emit('error'); assert.equal(f.progress.hidden, true);

// Errors keep download/reopen guidance; readiness is not recorded on failure.
f = fixture(); f.waiting.begin(f.request); f.complete(); f.player.emit('error');
assert.equal(f.visual.dataset.scene, 'failed'); assert.equal(f.records()[0].playable_at, null);
assert.match(f.nodes['#job-progress-detail'].textContent, /下载视频/);

// Reduced motion and blocked storage retain a usable player and in-memory timing.
f = fixture({reduced:true, blockedStorage:true}); f.waiting.begin(f.request); f.complete();
f.step(5000); f.player.readyState = 3; f.player.emit('canplay'); f.step(0);
assert.equal(f.visual.dataset.scene, 'ready'); assert.equal(f.clouds.paused, true);
assert.match(f.status.textContent, /00:05/);
console.log('PASS: continuous preparation timer/clouds; canplay gate; submission-to-playable clock; persistent first result; historical/source isolation; buffering recovery; late events and errors; reduced motion and blocked storage. No provider calls.');
