(() => {
  'use strict';
  const hero = document.querySelector('.hero');
  const video = document.querySelector('.hero-video');
  const toggle = document.querySelector('#scene-motion-toggle');
  if (!hero || !video || !toggle) return;
  // Preserve the approved artwork until its matching video exists.
  const source = video.dataset.src?.trim();
  if (!source) return;
  const foregroundPlayers = [...document.querySelectorAll('#preview-video,#detail-video')];

  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const sceneKey = 'prelude.cloud.motion.v1';
  function readScene() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(sceneKey));
      return saved && Number.isFinite(saved.time) && saved.time >= 0 && typeof saved.paused === 'boolean' && Number.isFinite(saved.savedAt) && Date.now() - saved.savedAt < 1800000 ? saved : null;
    } catch { return null; }
  }
  let savedScene = readScene();
  let visible = true;
  let userPaused = reducedMotion.matches || Boolean(savedScene?.paused);
  let loaded = false;
  let failed = false;
  let autoplayBlocked = false;
  let retryTimer = 0;
  let retries = 0;
  let watchdog = 0, lastTime = 0, lastAdvance = 0;
  const retryDelays = [1200, 3500];
  let revision = 0;
  video.muted = true;
  video.defaultMuted = true;
  toggle.hidden = false;

  function updateButton() {
    const paused = userPaused || autoplayBlocked || failed;
    toggle.setAttribute('aria-pressed', String(userPaused));
    toggle.querySelector('span').textContent = failed ? '重试动态' : paused ? '播放动态' : '暂停动态';
    toggle.querySelector('use').setAttribute('href', paused ? '#i-play' : '#i-pause');
    if (failed) toggle.title = '云海暂未加载，点击重新播放';
    else toggle.removeAttribute('title');
  }
  function showPoster() { hero.classList.remove('video-ready'); }
  function clearRetry() { clearTimeout(retryTimer); retryTimer = 0; }
  function clearWatchdog() { clearInterval(watchdog); watchdog = 0; }
  function watchPlayback() {
    if (watchdog) return;
    lastTime = video.currentTime; lastAdvance = Date.now();
    watchdog = setInterval(() => {
      if (!wantsPlayback() || autoplayBlocked || failed) { clearWatchdog(); return; }
      if (Math.abs(video.currentTime - lastTime) > .3) {
        lastTime = video.currentTime; lastAdvance = Date.now(); retries = 0;
      } else if (Date.now() - lastAdvance > 15000) mediaFailed();
    }, 1500);
  }
  function wantsPlayback() { return !userPaused && visible && !document.hidden &&
    !foregroundPlayers.some(player => !player.paused && !player.ended); }
  function retryPlayback() {
    clearRetry();
    failed = false;
    autoplayBlocked = false;
    loaded = false;
    syncPlayback();
  }
  function scheduleRetry() {
    if (retryTimer || retries >= retryDelays.length || !wantsPlayback()) return;
    retryTimer = setTimeout(() => { retries++; retryPlayback(); }, retryDelays[retries]);
  }
  function mediaFailed() {
    if (failed) return;
    failed = true;
    clearWatchdog();
    revision++;
    // A transient media error must not permanently disable the sky or its control.
    hero.dataset.motion = 'retry';
    video.pause();
    showPoster();
    updateButton();
    scheduleRetry();
  }
  function restoreScene() {
    if (!savedScene || !Number.isFinite(video.duration) || video.duration <= 0) return;
    const elapsed = savedScene.paused ? 0 : Math.max(0, (Date.now() - savedScene.savedAt) / 1000);
    video.currentTime = (savedScene.time + elapsed) % video.duration;
    savedScene = null;
  }
  video.addEventListener('loadedmetadata', restoreScene);
  video.addEventListener('seeked', () => { if (video.readyState >= 2 && !reducedMotion.matches) hero.classList.add('video-ready'); });
  async function syncPlayback() {
    const request = ++revision;
    if (!loaded && !failed && (wantsPlayback() || savedScene)) {
      loaded = true;
      hero.dataset.motion = 'loading';
      video.preload = 'auto';
      video.src = source;
      video.load();
    }
    if (!wantsPlayback()) {
      clearRetry();
      clearWatchdog();
      video.pause();
      updateButton();
      return;
    }
    if (failed) { scheduleRetry(); updateButton(); return; }
    watchPlayback();
    try {
      await video.play();
      if (request !== revision) return;
      autoplayBlocked = false;
      updateButton();
    } catch (error) {
      if (request !== revision || error.name === 'AbortError') return;
      if (error.name === 'NotAllowedError') {
        // Browser autoplay restrictions are separate from an explicit user pause.
        autoplayBlocked = true;
        clearWatchdog();
        hero.dataset.motion = 'blocked';
        updateButton();
      } else mediaFailed();
    }
  }
  video.addEventListener('playing', () => {
    if (!wantsPlayback()) { video.pause(); return; }
    failed = autoplayBlocked = false;
    clearRetry();
    hero.dataset.motion = 'playing';
    hero.classList.add('video-ready');
    updateButton();
  });
  video.addEventListener('pause', () => {
    if (!failed) hero.dataset.motion = userPaused ? 'paused' : 'idle';
    updateButton();
  });
  video.addEventListener('error', mediaFailed);
  toggle.addEventListener('click', () => {
    if (failed || autoplayBlocked) {
      userPaused = false;
      retries = 0;
      retryPlayback();
      return;
    }
    userPaused = !userPaused;
    syncPlayback();
  });
  document.addEventListener('pointerdown', event => {
    if (autoplayBlocked && wantsPlayback() && !toggle.contains(event.target)) syncPlayback();
  }, {passive:true});
  new IntersectionObserver(entries => {
    visible = entries[0].isIntersecting && entries[0].intersectionRatio > 0.08;
    syncPlayback();
  }, { rootMargin:'-80px 0px 0px 0px', threshold:[0,0.08] }).observe(hero);
  document.addEventListener('visibilitychange', syncPlayback);
  foregroundPlayers.forEach(player => ['play','pause','ended','emptied'].forEach(name => player.addEventListener(name, syncPlayback)));
  window.addEventListener('online', () => {
    if (failed && wantsPlayback()) { retries = 0; retryPlayback(); }
  });
  reducedMotion.addEventListener('change', () => {
    userPaused = reducedMotion.matches;
    if (userPaused) showPoster();
    syncPlayback();
  });
  window.addEventListener('pagehide', () => {
    try { sessionStorage.setItem(sceneKey, JSON.stringify({time:video.currentTime, paused:userPaused, savedAt:Date.now()})); } catch {}
    clearRetry();
    clearWatchdog();
    revision++; video.pause();
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { savedScene = readScene(); if (savedScene) userPaused = reducedMotion.matches || savedScene.paused; restoreScene(); }
    syncPlayback();
  });
  updateButton();
  syncPlayback();
})();
