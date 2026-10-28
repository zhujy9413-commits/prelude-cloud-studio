/* Private single-transfer playback: incremental when supported, cached for replay. */
(() => {
  'use strict';
  const cache = new Map(), players = new Map(), downloads = new WeakMap();
  const fileLimit = 32 * 1024 * 1024, cacheLimit = 48 * 1024 * 1024;
  let sequence = 0;
  const sourceOf = job => job.playback_url || job.video_url;
  const privateSource = source => /^\/api\/generations\/[a-f0-9-]{36}\/(?:preview|video|stream)$/.test(source || '');
  function plan(job) {
    const size=job.playback_size_bytes || job.original_size_bytes || 0;
    let incremental=false;
    try { incremental=Boolean(privateSource(job.stream_url) && job.stream_url.split('/')[3]===job.id &&
      /^video\/mp4; codecs="avc1\.[a-f0-9]{6}(?:, mp4a\.40\.2)?"$/i.test(job.stream_type||'') &&
      window.MediaSource?.isTypeSupported(job.stream_type) && (job.stream_size_bytes||size)<=fileLimit); } catch {}
    return incremental ? {source:job.stream_url,size:job.stream_size_bytes,mime:job.stream_type} : {source:sourceOf(job),size,native:size>fileLimit};
  }
  const keyOf = job => {const p=plan(job);return `${p.source}:${p.size||''}`;};
  function dispose(entry) {
    entry.controller.abort();
    entry.stream?.detach();
    clearTimeout(entry.idle); clearTimeout(entry.deadline);
    if (entry.url) URL.revokeObjectURL(entry.url);
    if(cache.get(entry.key)===entry)cache.delete(entry.key);
  }
  function trim() {
    const unpinned = [...cache.values()].filter(e => !e.users.size).sort((a,b) => a.used-b.used);
    let bytes = [...cache.values()].reduce((n,e) => n+e.bytes,0);
    for (const entry of unpinned) {
      if (cache.size <= 3 && bytes <= cacheLimit) break;
      bytes -= entry.bytes; dispose(entry);
    }
  }
  function release(player) {
    const state = players.get(player);
    if (state) {
      players.delete(player); state.entry.users.delete(state);
      state.cleanup?.();
      if(state.streaming)state.entry.stream?.detach();
      if (!state.entry.url && !state.entry.users.size) dispose(state.entry);
    }
    player.pause(); player.removeAttribute('src'); delete player.dataset.playbackSource;
    delete player.dataset.playbackReady;delete player.dataset.playbackManaged;
    player.load(); trim();
  }
  function clear() {
    for (const player of [...players.keys()]) release(player);
    for (const entry of [...cache.values()]) dispose(entry);
  }
  function invalidate(job) {
    const source = sourceOf(job);
    const match = value => value === source || value.startsWith(`/api/generations/${job.id}/`);
    for (const [player,state] of [...players]) if (match(state.source)) release(player);
    for (const entry of [...cache.values()]) if (match(entry.source)) dispose(entry);
  }
  function notify(entry) {
    entry.stream?.check();
    for (const state of entry.users) state.handlers.progress?.({loaded:entry.bytes,total:entry.total,ready:state.player.dataset.playbackReady==='true',mode:state.native?'native':state.streaming?'incremental':'complete'});
  }
  function startStream(state, mime) {
    const {entry,player}=state, media=new window.MediaSource(), queue=[];
    let buffer=null,done=false,detached=false,opened=false;
    const url=URL.createObjectURL(media), started=Date.now(), samples=[{time:started,bytes:0}];
    const valid=()=>!detached && players.get(player)===state;
    const ready=()=>{
      if(!valid()||!buffer||!buffer.buffered.length)return;
      const now=Date.now(), age=(now-started)/1000;
      samples.push({time:now,bytes:entry.bytes});
      while(samples.length>2&&samples[1].time<now-6000)samples.shift();
      const first=samples[0], windowAge=(now-first.time)/1000;
      const rate=Math.min(entry.bytes/Math.max(age,.001),(entry.bytes-first.bytes)/Math.max(windowAge,.001))*.75;
      const time=player.currentTime||0, duration=state.duration;
      let end=time;
      for(let i=0;i<buffer.buffered.length;i++)if((buffer.buffered.start?.(i)??0)<=time+.15&&buffer.buffered.end(i)>time){end=buffer.buffered.end(i);break;}
      const ahead=end-time;
      const complete=done&&!queue.length&&!buffer.updating;
      const enough=age>=1.2&&entry.total&&rate>0&&ahead>=Math.min(3,duration)&&
        (entry.total-entry.bytes)/rate<=Math.max(0,duration-time-2);
      if((complete||enough)&&player.dataset.playbackReady!=='true'){
        player.dataset.playbackReady='true';state.handlers.ready?.();
      }
    };
    const fallback=()=>{
      if(!valid())return;
      const position=player.currentTime||0, playing=!player.paused;
      detach();state.streaming=false;player.dataset.playbackReady='false';
      state.position=position;state.wasPlaying=playing;
      player.pause();player.removeAttribute('src');player.load();state.handlers.loading?.();
      if(entry.url)attachBlob(state,entry.url,position,playing);
    };
    const pump=()=>{
      if(!valid()||!buffer||buffer.updating)return;
      try {
        if(queue.length)buffer.appendBuffer(queue.shift());
        else if(done&&media.readyState==='open')media.endOfStream();
        ready();
      } catch {fallback();}
    };
    function detach(){
      detached=true;queue.length=0;
      media.removeEventListener('sourceopen',open);media.removeEventListener('sourceclose',closed);
      if(buffer){buffer.removeEventListener('updateend',pump);buffer.removeEventListener('error',fallback);}
      URL.revokeObjectURL(url);
    }
    function open(){
      if(!valid()||opened)return;
      opened=true;
      try {media.duration=state.duration;buffer=media.addSourceBuffer(mime);buffer.addEventListener('updateend',pump);buffer.addEventListener('error',fallback);pump();}
      catch {fallback();}
    }
    function closed(){if(valid()&&!done)fallback();}
    media.addEventListener('sourceopen',open);media.addEventListener('sourceclose',closed);
    state.streaming=true;state.streamFallback=fallback;player.dataset.playbackReady='false';
    player.src=url;player.load();
    return {append:chunk=>{if(valid()){queue.push(chunk);pump();}},finish:()=>{done=true;pump();},check:ready,detach};
  }
  function attachBlob(state,url,position=0,resume=false) {
    if(players.get(state.player)!==state)return;
    const player=state.player;
    player.dataset.playbackReady='true';player.src=url;player.load();
    if(position>0&&player.addEventListener)player.addEventListener('loadedmetadata',()=>{
      if(players.get(player)!==state)return;
      player.currentTime=Math.min(position,player.duration||position);
      if(resume)player.play()?.catch(()=>{});
    },{once:true});
    state.handlers.ready?.();
  }
  async function receive(entry) {
    const touch = () => { clearTimeout(entry.idle); entry.idle = setTimeout(() => entry.controller.abort(), 25000); };
    entry.deadline = setTimeout(() => entry.controller.abort(), 240000);
    touch();
    try {
      const response = await fetch(entry.source, {credentials:'same-origin',cache:'no-cache',redirect:'error',signal:entry.controller.signal});
      if (!response.ok || !/^video\/mp4(?:;|$)/i.test(response.headers.get('Content-Type') || '')) throw Error('media');
      const length = Number(response.headers.get('Content-Length'));
      entry.total = Number.isFinite(length) && length > 0 ? length : null;
      if (entry.total > fileLimit) throw Error('size');
      const chunks = [];
      if (response.body?.getReader) {
        const reader = response.body.getReader();
        while (true) {
          const {done,value} = await reader.read();
          if (done) break;
          entry.bytes += value.byteLength;
          if (entry.bytes > fileLimit) { await reader.cancel(); throw Error('size'); }
          chunks.push(value); touch(); notify(entry);
          entry.stream?.append(value);
        }
      } else {
        const value = new Uint8Array(await response.arrayBuffer());
        entry.bytes = value.byteLength;
        if (entry.bytes > fileLimit) throw Error('size');
        chunks.push(value); notify(entry);
        entry.stream?.append(value);
      }
      if (entry.bytes < 32 || entry.total && entry.bytes !== entry.total) throw Error('incomplete');
      if (entry.controller.signal.aborted) throw Error('aborted');
      entry.url = URL.createObjectURL(new Blob(chunks,{type:'video/mp4'}));
      entry.stream?.finish();
      entry.total = entry.bytes; notify(entry); trim();
      return entry.url;
    } catch {
      entry.stream?.detach();
      entry.controller.abort();
      if (cache.get(entry.key) === entry) cache.delete(entry.key);
      throw Error('成片暂时未能载入，请重新打开这条作品，或下载后观看');
    } finally { clearTimeout(entry.idle); clearTimeout(entry.deadline); }
  }
  function matches(player, job) {
    const state = players.get(player);
    return Boolean(state && state.key === keyOf(job));
  }
  function load(player, job, handlers = {}) {
    const selected=plan(job), source = sourceOf(job), key = keyOf(job), old = players.get(player);
    if (old?.key === key && cache.get(key) === old.entry) {
      old.handlers = handlers;
      handlers.progress?.({loaded:old.entry.bytes,total:old.entry.total});
      if (old.entry.url || player.dataset.playbackReady==='true') handlers.ready?.();
      return;
    }
    release(player);
    if (!privateSource(source) || !privateSource(selected.source)) {
      handlers.error?.('成片地址暂不可用，请重新打开作品'); return;
    }
    let entry = cache.get(key);
    const fresh=!entry;
    if (fresh) {
      entry = {key,source:selected.source,controller:new AbortController(),users:new Set(),bytes:0,total:null,url:null,used:++sequence};
      cache.set(key,entry);
    }
    entry.used = ++sequence;
    const state = {key,source,entry,handlers,player,duration:job.duration,native:selected.native};
    entry.users.add(state); players.set(player,state);
    player.dataset.playbackSource = source; player.preload = 'auto'; player.poster = job.poster_url || '';
    player.dataset.playbackManaged='true';player.dataset.playbackReady='false';
    const mediaError=()=>{
      if(players.get(player)!==state)return;
      if(state.streaming){state.streamFallback();return;}
      state.handlers.error?.('成片暂时未能播放，可以下载原画质后观看');
    };
    const canplay=()=>{if(players.get(player)===state&&player.dataset.playbackReady==='true')state.handlers.ready?.();};
    player.addEventListener?.('error',mediaError);player.addEventListener?.('canplay',canplay);
    state.cleanup=()=>{player.removeEventListener?.('error',mediaError);player.removeEventListener?.('canplay',canplay);};
    if(selected.native){
      player.dataset.playbackReady='true';player.src=source;player.load();
      handlers.progress?.({loaded:0,total:selected.size,mode:'native',ready:false});return;
    }
    if(fresh){
      if(selected.mime)try{entry.stream=startStream(state,selected.mime);}catch{state.streaming=false;}
      entry.promise=receive(entry);
    }
    handlers.progress?.({loaded:entry.bytes,total:entry.total});
    entry.promise.then(url => {
      if (players.get(player) !== state) return;
      if(!state.streaming)attachBlob(state,url,state.position,state.wasPlaying);
    }).catch(error => {
      if (players.get(player) === state) state.handlers.error?.(error.message);
    });
  }
  function bindDownload(link,job,original=false){
    const source=original?job.video_url:sourceOf(job), prior=downloads.get(link);
    if(prior)link.removeEventListener('click',prior);
    if(!privateSource(source)){link.removeAttribute('href');downloads.delete(link);return;}
    link.href=source+'?download=1';
    const filename=`prelude-${original||!job.playback_optimized?'original':'flow'}-${job.id}.mp4`;
    link.download=filename;
    const handler=event=>{
      if(event.defaultPrevented||event.button>0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
      const entry=cache.get(keyOf(job));
      // Reuse only the exact downloaded file, never silently substitute the compressed stream.
      if(!entry?.url||entry.source!==source)return;
      event.preventDefault();
      const anchor=document.createElement('a');anchor.href=entry.url;anchor.download=filename;
      document.body.append(anchor);anchor.click();anchor.remove();
    };
    downloads.set(link,handler);link.addEventListener('click',handler);
  }
  window.PreludePlayback = {load,release,clear,invalidate,matches,bindDownload};
  window.addEventListener('pagehide',clear);
})();
