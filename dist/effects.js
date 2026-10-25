(() => {
  'use strict';
  const hero = document.querySelector('.hero');
  const video = document.querySelector('.hero-video');
  const toggle = document.querySelector('#scene-motion-toggle');
  if (!hero || !video || !toggle) return;
  // Preserve the approved artwork until its matching video exists.
  const source = video.dataset.src.trim();
  if (!source) return;

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
  let revision = 0;
  video.muted = true;
  video.defaultMuted = true;
  toggle.hidden = false;

  function updateButton() {
    const paused = userPaused || video.paused;
    toggle.setAttribute('aria-pressed', String(userPaused));
    toggle.querySelector('span').textContent = paused ? '播放动态' : '暂停动态';
    toggle.querySelector('use').setAttribute('href', paused ? '#i-play' : '#i-pause');
  }
  function showPoster() { hero.classList.remove('video-ready'); }
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
    if (failed) return;
    if (!loaded && (!userPaused || savedScene)) {
      loaded = true;
      video.preload = 'auto';
      video.src = source;
      video.load();
    }
    if (userPaused || !visible || document.hidden) {
      video.pause();
      updateButton();
      return;
    }
    try {
      await video.play();
      if (request !== revision) return;
      updateButton();
    } catch (error) {
      if (request !== revision || error.name === 'AbortError') return;
      // A blocked autoplay offers an explicit play action over the original image.
      userPaused = true;
      updateButton();
    }
  }
  video.addEventListener('playing', () => {
    if (!userPaused && visible && !document.hidden) hero.classList.add('video-ready');
    updateButton();
  });
  video.addEventListener('pause', updateButton);
  video.addEventListener('error', () => {
    failed = true;
    revision++;
    video.pause();
    showPoster();
    toggle.hidden = true;
  });
  toggle.addEventListener('click', () => {
    userPaused = !userPaused;
    syncPlayback();
  });
  new IntersectionObserver(entries => {
    visible = entries[0].isIntersecting && entries[0].intersectionRatio > 0.08;
    syncPlayback();
  }, { rootMargin:'-80px 0px 0px 0px', threshold:[0,0.08] }).observe(hero);
  document.addEventListener('visibilitychange', syncPlayback);
  reducedMotion.addEventListener('change', () => {
    userPaused = reducedMotion.matches;
    if (userPaused) showPoster();
    syncPlayback();
  });
  window.addEventListener('pagehide', () => {
    try { sessionStorage.setItem(sceneKey, JSON.stringify({time:video.currentTime, paused:userPaused, savedAt:Date.now()})); } catch {}
    revision++; video.pause();
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { savedScene = readScene(); if (savedScene) userPaused = reducedMotion.matches || savedScene.paused; restoreScene(); }
    syncPlayback();
  });
  updateButton();
  syncPlayback();
})();
