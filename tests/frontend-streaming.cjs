// Controlled MSE and network timing; the real playback module never calls a model.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('dist/playback.js','utf8');
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
class Target{
 constructor(){this.events={};}
 addEventListener(name,fn){(this.events[name]??=new Set()).add(fn);}
 removeEventListener(name,fn){this.events[name]?.delete(fn);}
 emit(name){for(const fn of [...(this.events[name]||[])])fn();}
}
class Player extends Target{
 constructor(){super();this.attrs={};this.dataset={};this.currentTime=0;this.paused=true;this.loads=0;}
 get src(){return this.attrs.src;}set src(v){this.attrs.src=v;}
 removeAttribute(k){delete this.attrs[k];}load(){this.loads++;}pause(){this.paused=true;}
}
const id='12345678-1234-1234-1234-123456789abc';
const job={id,duration:15,playback_optimized:true,playback_url:`/api/generations/${id}/preview`,video_url:`/api/generations/${id}/video`,
 stream_url:`/api/generations/${id}/stream`,stream_type:'video/mp4; codecs="avc1.64001f, mp4a.40.2"',stream_size_bytes:1000,playback_size_bytes:990,original_size_bytes:4000};
function fixture({supported=true,badBuffer=false,gap=false}={}){
 let now=0,sequence=0;const calls=[],urls=[],revoked=[],pending=[],timers=new Map(),media=[];let reads=0;
 class Buffer extends Target{
  constructor(){super();this.updating=false;this.end=0;this.buffered={get length(){return gap?2:1;},start:i=>gap&&i===1?10:0,end:i=>gap&&i===0?1:this.end};}
  appendBuffer(value){if(badBuffer)throw Error('unsupported decoder');this.updating=true;this.end+=value.byteLength*15/1000;Promise.resolve().then(()=>{this.updating=false;this.emit('updateend');});}
 }
 class Media extends Target{
  static isTypeSupported(){return supported;}
  constructor(){super();this.readyState='closed';media.push(this);}
  addSourceBuffer(){this.buffer=new Buffer();return this.buffer;}
  endOfStream(){this.readyState='ended';}
 }
 const win=new Target();win.MediaSource=Media;
 const saved=[];
 const document={body:{append(){}},createElement:()=>({click(){saved.push({href:this.href,download:this.download});},remove(){}})};
 const context={window:win,document,Blob,AbortController,Date:{now:()=>now},URL:{createObjectURL:value=>{const url='blob:'+urls.length;urls.push({url,value});if(value instanceof Media)Promise.resolve().then(()=>{value.readyState='open';value.emit('sourceopen');});return url;},revokeObjectURL:url=>revoked.push(url)},
 setTimeout:(fn,ms)=>{const key=++sequence;timers.set(key,{fn,ms});return key;},clearTimeout:key=>timers.delete(key),
 fetch:async(url,options)=>{calls.push({url,options});return {ok:true,headers:{get:key=>key==='Content-Type'?'video/mp4':key==='Content-Length'?'1000':null},body:{getReader:()=>({read:()=>{const next=defer();pending.push(next);reads++;return next.promise;}})}}}};
 vm.runInNewContext(source,context);
 return {api:win.PreludePlayback,calls,urls,revoked,media,timers,saved,
  async chunk(size,ms){now+=ms;assert.ok(pending.length);pending.shift().resolve({done:false,value:new Uint8Array(size)});await flush();},
  async end(){pending.shift().resolve({done:true});await flush();}};
}
(async()=>{
 let f=fixture(),p=new Player(),q=new Player(),ready=0;
 f.api.load(p,job,{ready:()=>ready++});await flush();
 assert.equal(f.calls[0].url,job.stream_url);assert.equal(p.dataset.playbackReady,'false');
 await f.chunk(200,7000);assert.equal(p.dataset.playbackReady,'false');
 await f.chunk(500,17000);assert.equal(p.dataset.playbackReady,'false');
 await f.chunk(200,5000);assert.equal(p.dataset.playbackReady,'true');assert.ok(ready>0);
 assert.equal(f.calls.length,1);const streamingSource=p.src;
 // A second player waits for the same response; it cannot attach the first player's MediaSource.
 f.api.load(q,job);await flush();assert.equal(q.src,undefined);assert.equal(f.calls.length,1);
 await f.chunk(100,3000);await f.end();assert.equal(p.src,streamingSource);assert.notEqual(q.src,streamingSource);
 assert.equal(q.dataset.playbackReady,'true');assert.equal(f.media[0].readyState,'ended');
 const loads=p.loads;f.api.load(p,job);assert.equal(p.loads,loads);
 f.api.release(q);f.api.load(q,job);await flush();assert.equal(f.calls.length,1);
 f.api.clear();assert.equal(p.src,undefined);assert.equal(q.src,undefined);assert.equal(f.timers.size,0);

 // A distant buffered range cannot conceal a gap in front of the playhead.
 f=fixture({gap:true});p=new Player();f.api.load(p,job);await flush();await f.chunk(900,30000);
 assert.equal(p.dataset.playbackReady,'false');f.api.clear();await f.end();

 // Unsupported MSE retains full-file loading and the original preview bytes.
 f=fixture({supported:false});p=new Player();f.api.load(p,job);await flush();assert.equal(f.calls[0].url,job.playback_url);
 await f.chunk(1000,1000);assert.equal(p.src,undefined);await f.end();assert.match(p.src,/^blob:/);
 const compressed=new Player(),original=new Player();compressed.href='';original.href='';
 f.api.bindDownload(compressed,job);f.api.bindDownload(original,job,true);
 let prevented=false;for(const fn of compressed.events.click)fn({button:0,preventDefault(){prevented=true;}});
 assert.equal(prevented,true);assert.equal(f.saved[0].href,p.src);assert.equal(f.calls.length,1);
 assert.equal(original.href,job.video_url+'?download=1');
 prevented=false;for(const fn of original.events.click)fn({button:0,preventDefault(){prevented=true;}});
 assert.equal(prevented,false); // Never substitutes compressed cache bytes for an original-quality download.

 // A SourceBuffer/decoder failure completes the same transfer before falling back.
 f=fixture({badBuffer:true});p=new Player();let loading=0,errors=0;
 f.api.load(p,job,{loading:()=>loading++,error:()=>errors++});await flush();await f.chunk(1000,1000);await f.end();
 assert.equal(f.calls.length,1);assert.equal(loading,1);assert.equal(errors,0);assert.equal(p.dataset.playbackReady,'true');
 assert.ok(f.urls.find(x=>x.url===p.src).value instanceof Blob);

 // Large validated files use authenticated native Range playback, with no full-file memory allocation.
 f=fixture();p=new Player();f.api.load(p,{...job,stream_size_bytes:40*1024*1024,playback_size_bytes:40*1024*1024});await flush();
 assert.equal(f.calls.length,0);assert.equal(p.src,job.playback_url);assert.equal(p.dataset.playbackReady,'true');f.api.clear();

 // Switching away cancels the old stream and late chunks cannot replace the selected film.
 f=fixture();p=new Player();f.api.load(p,job);await flush();const signal=f.calls[0].options.signal;
 f.api.release(p);assert.equal(signal.aborted,true);await f.chunk(1000,1000);await f.end();assert.equal(p.src,undefined);
 console.log('PASS: conservative playback gate, one transfer, dual-player/cache reuse, unsupported/decoder fallback, native large-file playback, cancellation and cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
