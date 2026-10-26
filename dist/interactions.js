(() => {
  'use strict';
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const waves=new WeakMap();
  const pressed=new Set();
  function release(){pressed.forEach(target=>target.classList.remove('is-pressing'));pressed.clear();}
  function press(target,event){
    if(target.matches('.skip-link')||target.disabled||target.getAttribute('aria-disabled')==='true')return;
    target.classList.add('press-surface','is-pressing');pressed.add(target);
    if(reduced.matches)return;
    const previous=waves.get(target);if(previous){clearTimeout(previous.timer);previous.wave.remove();}
    const rect=target.getBoundingClientRect(),wave=document.createElement('i');
    wave.className='press-wave';wave.setAttribute('aria-hidden','true');
    wave.style.setProperty('--press-x',event.type==='keydown'?'50%':`${event.clientX-rect.left}px`);
    wave.style.setProperty('--press-y',event.type==='keydown'?'50%':`${event.clientY-rect.top}px`);
    target.classList.add('press-surface');target.append(wave);
    const timer=setTimeout(()=>{wave.remove();waves.delete(target);},540);
    waves.set(target,{wave,timer});
  }
  document.addEventListener('pointerdown',event=>{
    if(event.button!==0)return;
    const target=event.target.closest('button,a[href]');if(target)press(target,event);
  },{passive:true});
  document.addEventListener('keydown',event=>{
    if(event.repeat||!['Enter',' '].includes(event.key))return;
    const target=event.target.closest('button,a[href]');
    if(target&&(event.key==='Enter'||target.tagName==='BUTTON'))press(target,event);
  });
  document.addEventListener('pointerup',release,{passive:true});
  document.addEventListener('pointercancel',release,{passive:true});
  document.addEventListener('keyup',release);
  window.addEventListener('blur',release);
  window.addEventListener('pagehide',release);
})();
