// Executes the real short-film loader with controlled network/players. No model calls.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('dist/playback.js','utf8');
const flush = async () => {for(let i=0;i<12;i++)await Promise.resolve();};
const deferred = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
class Player {
 constructor(){this.dataset={};this.attrs={};this.loads=0;this.paused=true;}
 set src(value){this.attrs.src=value;}get src(){return this.attrs.src;}
 removeAttribute(k){delete this.attrs[k];}pause(){this.paused=true;}load(){this.loads++;}
}
const job = n => ({id:n.toString(16).padStart(36,'0'),playback_url:`/api/generations/${n.toString(16).padStart(36,'0')}/preview`,playback_size_bytes:128});
function fixture() {
 const win={events:{},addEventListener(k,fn){this.events[k]=fn;}},calls=[],urls=[],revoked=[],responses=[],timers=new Map();let sequence=0;
 const context={window:win,AbortController,Blob,Map,Set,URL:{createObjectURL:blob=>{const url='blob:test-'+urls.length;urls.push({url,blob});return url;},revokeObjectURL:url=>revoked.push(url)},
 setTimeout:(fn,ms)=>{const id=++sequence;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
 fetch:async (url,options)=>{calls.push({url,options});const response=responses.shift();if(response instanceof Error)throw response;return response||reply();}};
 vm.runInNewContext(source,context);
 return {api:win.PreludePlayback,win,calls,urls,revoked,responses,timers};
}
function reply({status=200,type='video/mp4',length=128,reader=null}={}) {
 return {ok:status===200,headers:{get:k=>k==='Content-Type'?type:k==='Content-Length'?String(length):null},
   body:reader?{getReader:()=>reader}:null,arrayBuffer:async()=>new ArrayBuffer(length)};
}
(async()=>{
 let f=fixture(),p=new Player(),q=new Player(),progress=[],errors=[];
 const chunk=deferred();let reads=0;
 f.responses.push(reply({reader:{read:()=>reads++===0?Promise.resolve({done:false,value:new Uint8Array(64)}):reads===2?chunk.promise:Promise.resolve({done:true}),cancel:async()=>{}}}));
 f.api.load(p,job(1),{progress:x=>progress.push(x),error:x=>errors.push(x)});
 f.api.load(q,job(1),{error:x=>errors.push(x)});await flush();
 assert.equal(f.calls.length,1);assert.equal(p.src,undefined);assert.equal(q.src,undefined);
 assert.equal(progress.at(-1).loaded,64);assert.equal(f.calls[0].options.credentials,'same-origin');
 chunk.resolve({done:false,value:new Uint8Array(64)});await flush();
 assert.equal(p.src,q.src);assert.match(p.src,/^blob:/);assert.equal(f.urls.length,1);assert.equal(f.urls[0].blob.size,128);
 const loads=p.loads;f.api.load(p,job(1));await flush();assert.equal(p.loads,loads);assert.equal(f.calls.length,1);
 f.api.release(p);assert.equal(p.src,undefined);f.api.load(p,job(1));await flush();assert.equal(f.calls.length,1);
 f.api.invalidate({id:job(1).id,playback_url:null,video_url:null});
 assert.equal(p.src,undefined);assert.equal(q.src,undefined);assert.ok(f.revoked.includes('blob:test-0'));
 assert.deepEqual(errors,[]);assert.equal(f.timers.size,0);

 // Failed transfers are not cached or automatically retried; explicit reopen can retry.
 f=fixture();p=new Player();errors=[];f.responses.push(reply({status:404}));
 f.api.load(p,job(1),{error:x=>errors.push(x)});await flush();assert.equal(errors.length,1);assert.equal(p.src,undefined);assert.equal(f.calls.length,1);
 f.api.load(p,job(1));await flush();assert.match(p.src,/blob:/);assert.equal(f.calls.length,2);
 f.api.clear();assert.equal(p.src,undefined);assert.equal(f.revoked.length,1);

 // Releasing the other player from a failed old transfer cannot evict its replacement.
 f=fixture();p=new Player();q=new Player();f.responses.push(reply({status:404}));
 f.api.load(p,job(1));f.api.load(q,job(1));await flush();
 f.api.load(p,job(1));await flush();f.api.load(q,job(1));await flush();
 assert.equal(f.calls.length,2);assert.equal(p.src,q.src);

 // Reject HTML/login responses, oversized media, and truncated streams.
 for(const response of [reply({type:'text/html'}),reply({length:33*1024*1024}),reply({reader:{read:async()=>({done:true})}})]){
   f=fixture();p=new Player();errors=[];f.responses.push(response);f.api.load(p,job(1),{error:x=>errors.push(x)});await flush();
   assert.equal(errors.length,1);assert.equal(f.urls.length,0);assert.equal(f.calls.length,1);
 }
 f=fixture();p=new Player();errors=[];f.api.load(p,{playback_url:'https://other.example/file.mp4'},{error:x=>errors.push(x)});await flush();
 assert.equal(f.calls.length,0);assert.equal(errors.length,1);

 // Switching films mid-transfer aborts/discards the old response and cannot change the current film.
 f=fixture();p=new Player();const pending=deferred();
 f.responses.push(reply({reader:{read:()=>pending.promise}}));f.api.load(p,job(1));await flush();
 f.api.load(p,job(2));await flush();const current=p.src;assert.equal(f.calls[0].options.signal.aborted,true);
 pending.resolve({done:true});await flush();assert.equal(p.src,current);assert.equal(f.urls.length,1);

 // Only a bounded history is retained; page exit revokes active private blob URLs.
 f=fixture();p=new Player();for(let n=1;n<=5;n++){f.api.load(p,job(n));await flush();f.api.release(p);}
 assert.equal(f.calls.length,5);assert.equal(f.revoked.length,2);
 f.api.load(p,job(5));await flush();assert.equal(f.calls.length,5);
 f.win.events.pagehide();assert.equal(p.src,undefined);assert.equal(f.revoked.length,5);
 console.log('PASS: one complete private transfer; shared preview/details; replay and reopen without fetch/reload; progressive byte counts; logout/deletion/eviction cleanup; failed/oversized/incomplete responses; switching-film cancellation; bounded memory. No provider calls.');
})().catch(error=>{console.error(error);process.exitCode=1;});
