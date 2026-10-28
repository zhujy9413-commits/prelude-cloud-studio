const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('dist/effects.js', 'utf8');
class Target {
 constructor(){this.events={};this.dataset={};this.attrs={};this.hidden=false;this.classList={items:new Set(),add(...a){a.forEach(x=>this.items.add(x))},remove(...a){a.forEach(x=>this.items.delete(x))},contains(x){return this.items.has(x)}};}
 addEventListener(name,fn){(this.events[name]??=[]).push(fn)}
 emit(name,event={}){for(const fn of this.events[name]||[])fn(event)}
 setAttribute(k,v){this.attrs[k]=v}
 getAttribute(k){return this.attrs[k]??null}
 removeAttribute(k){delete this.attrs[k]}
 contains(node){return node===this}
}
function fixture({mode='ok', reduced=false, saved=null}={}){
 const hero=new Target(),video=new Target(),toggle=new Target(),doc=new Target(),win=new Target(),query=new Target();
 const span={textContent:''},use=new Target();toggle.querySelector=s=>s==='span'?span:use;
 query.matches=reduced;
 video.dataset.src='assets/cloudscape-loop.mp4';video.paused=true;video.currentTime=0;video.duration=9;video.readyState=0;video.loads=0;video.plays=0;video.mode=mode;
 video.load=()=>{video.loads++;video.emit('loadedmetadata')};
 video.pause=()=>{video.paused=true;video.emit('pause')};
 video.play=()=>{video.plays++;if(video.mode==='network'){video.emit('error');return Promise.reject({name:'NotSupportedError'})}if(video.mode==='blocked')return Promise.reject({name:'NotAllowedError'});video.paused=false;video.readyState=4;video.emit('playing');return Promise.resolve()};
 const selectors={'.hero':hero,'.hero-video':video,'#scene-motion-toggle':toggle};doc.querySelector=s=>selectors[s];const foreground=new Target();foreground.paused=true;foreground.ended=false;doc.querySelectorAll=()=>[foreground];doc.hidden=false;
 let sequence=0,observer,now=Date.now();const timers=new Map(),intervals=new Map();const Clock=class extends Date{static now(){return now}};
 const storage=new Map(saved?[['prelude.cloud.motion.v1',JSON.stringify(saved)]]:[]);
 const context={document:doc,window:win,matchMedia:()=>query,sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)},IntersectionObserver:class{constructor(fn){observer=fn}observe(){}},setTimeout:(fn,delay)=>{let id=++sequence;timers.set(id,{fn,delay});return id},clearTimeout:id=>timers.delete(id),Date:Clock,Number,Math,Boolean,JSON,setInterval:fn=>{const id=++sequence;intervals.set(id,fn);return id},clearInterval:id=>intervals.delete(id)};
 vm.runInNewContext(source,context);
 return {hero,video,toggle,span,doc,win,query,timers,storage,foreground,step(ms){now+=ms;[...intervals.values()].forEach(fn=>fn())},tick(){const [id,t]=timers.entries().next().value;timers.delete(id);t.fn()},intersect(visible){observer([{isIntersecting:visible,intersectionRatio:visible?1:0}])}};
}
const flush=async()=>{await Promise.resolve();await Promise.resolve()};
(async()=>{
 let f=fixture({mode:'network'});await flush();assert.equal(f.toggle.hidden,false);assert.equal(f.span.textContent,'重试动态');assert.equal(f.toggle.getAttribute('aria-pressed'),'false');assert.equal(f.timers.size,1);f.video.mode='ok';f.tick();await flush();assert.equal(f.video.paused,false);assert.equal(f.hero.classList.contains('video-ready'),true);assert.equal(f.timers.size,0);
 f=fixture({mode:'network'});await flush();f.tick();await flush();f.tick();await flush();assert.equal(f.timers.size,0);assert.equal(f.video.loads,3);f.video.mode='ok';f.toggle.emit('click');await flush();assert.equal(f.video.paused,false);assert.equal(f.video.loads,4);
 f=fixture({mode:'network'});await flush();f.doc.hidden=true;f.doc.emit('visibilitychange');assert.equal(f.timers.size,0);f.doc.hidden=false;f.doc.emit('visibilitychange');assert.equal(f.timers.size,1);f.video.mode='ok';f.tick();await flush();assert.equal(f.video.paused,false);
 f=fixture({mode:'blocked'});await flush();assert.equal(f.toggle.getAttribute('aria-pressed'),'false');assert.equal(f.span.textContent,'播放动态');assert.equal(f.timers.size,0);f.video.mode='ok';f.toggle.emit('click');await flush();assert.equal(f.video.paused,false);assert.equal(f.toggle.getAttribute('aria-pressed'),'false');
 f=fixture();await flush();f.toggle.emit('click');assert.equal(f.video.paused,true);assert.equal(f.toggle.getAttribute('aria-pressed'),'true');f.doc.emit('visibilitychange');assert.equal(f.video.paused,true);f.toggle.emit('click');await flush();assert.equal(f.video.paused,false);f.intersect(false);assert.equal(f.video.paused,true);f.intersect(true);await flush();assert.equal(f.video.paused,false);
 f=fixture({reduced:true});assert.equal(f.video.loads,0);assert.equal(f.video.plays,0);f.toggle.emit('click');await flush();assert.equal(f.video.paused,false);
 f=fixture({saved:{time:3,paused:true,savedAt:Date.now()}});assert.equal(f.video.loads,1);assert.equal(f.video.currentTime,3);assert.equal(f.video.paused,true);assert.equal(f.video.plays,0);
 f=fixture({mode:'network'});await flush();f.win.emit('pagehide');assert.equal(f.timers.size,0);assert.equal(JSON.parse(f.storage.get('prelude.cloud.motion.v1')).paused,false);
 f=fixture();await flush();f.step(16000);assert.equal(f.span.textContent,'重试动态');assert.equal(f.timers.size,1);f.tick();await flush();f.step(16000);f.tick();await flush();f.step(16000);assert.equal(f.timers.size,0);assert.equal(f.video.loads,3);
 f=fixture();await flush();f.foreground.paused=false;f.foreground.emit('play');assert.equal(f.video.paused,true);f.foreground.paused=true;f.foreground.emit('pause');await flush();assert.equal(f.video.paused,false);
 console.log('PASS: foreground-player background suspension;  stalled playback watchdog and bounded retries;  transient failure recovery; bounded retries and manual retry; hidden-tab cancellation and resume; blocked autoplay; explicit pause; offscreen resume; reduced motion; paused-frame restoration; pagehide cleanup. No browser or provider requests.');
})().catch(e=>{console.error(e);process.exitCode=1});
