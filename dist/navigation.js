(() => {
  'use strict';
  const root=document.documentElement;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const native='onpagereveal' in window && 'onpageswap' in window;
  let leaving=false, watchdog=null;
  function reset(){leaving=false;root.classList.remove('nav-leaving','nav-entering');clearTimeout(watchdog);}
  function arrive(){reset();if(reduced.matches)return;root.classList.add('nav-entering');setTimeout(()=>root.classList.remove('nav-entering'),480);}
  function isStudio(url){return /\/(?:index|workspace)\.html$/.test(url.pathname)||url.pathname.endsWith('/');}
  function sameDocument(url){return url.pathname===location.pathname && url.search===location.search;}
  // Older browsers get a short exit/entry fade. Native navigation, hashes,
  // modified clicks, downloads and external links retain their normal behavior.
  if(!native){
    try{
      const record=JSON.parse(sessionStorage.getItem('prelude.navigation')||'null');
      sessionStorage.removeItem('prelude.navigation');
      if(record && record.path===location.pathname+location.search && Date.now()-record.time<8000)arrive();
    }catch{}
    document.addEventListener('click',event=>{
      if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||reduced.matches)return;
      const link=event.target.closest?.('a[href]');
      if(!link||link.hasAttribute('download')||(link.target&&link.target!=='_self'))return;
      const url=new URL(link.href,location.href);
      if(url.origin!==location.origin||!isStudio(url)||sameDocument(url))return;
      event.preventDefault();if(leaving)return;leaving=true;
      try{sessionStorage.setItem('prelude.navigation',JSON.stringify({path:url.pathname+url.search,time:Date.now()}));}catch{}
      root.classList.add('nav-leaving');
      setTimeout(()=>{location.assign(url.href);},180);
      watchdog=setTimeout(reset,1600);
    });
  }
  window.addEventListener('pageshow',event=>{if(native)reset();else if(event.persisted)arrive();});
  function observeTransition(transition){
    if(!transition)return;
    // Resizing or a second navigation can legitimately cancel a snapshot.
    transition.ready.catch(()=>{});
    transition.finished.catch(()=>{});
  }
  window.addEventListener('pageswap',event=>observeTransition(event.viewTransition));
  window.addEventListener('pagereveal',event=>{reset();observeTransition(event.viewTransition);if(reduced.matches)event.viewTransition?.skipTransition();});
  reduced.addEventListener('change',()=>{if(reduced.matches)reset();});
})();
