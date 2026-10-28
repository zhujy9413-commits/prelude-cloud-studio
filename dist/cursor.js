(() => {
  'use strict';
  const fine = matchMedia('(hover: hover) and (pointer: fine)');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const contrast = matchMedia('(forced-colors: active)');
  const root = document.documentElement;
  const layer = document.createElement('div');
  layer.className = 'studio-cursor';
  layer.setAttribute('aria-hidden', 'true');
  layer.innerHTML = `<svg class="studio-cursor__trail" aria-hidden="true"><defs><linearGradient id="prelude-comet-gradient" gradientUnits="userSpaceOnUse"><stop stop-color="#f5eaff"/><stop offset=".45" stop-color="#cfb6f2" stop-opacity=".65"/><stop offset="1" stop-color="#aebdf1" stop-opacity="0"/></linearGradient></defs><path class="comet-bloom"/><path class="comet-line"/></svg><span class="studio-cursor__point"><svg class="studio-cursor__spark" viewBox="0 0 24 24"><path d="M12 1.5C12.9 8.9 15.1 11.1 22.5 12 15.1 12.9 12.9 15.1 12 22.5 11.1 15.1 8.9 12.9 1.5 12 8.9 11.1 11.1 8.9 12 1.5Z" fill="#f5ecff" stroke="#26304e" stroke-width=".8"/></svg><svg class="studio-cursor__arrow" viewBox="0 0 20 23"><path d="M1 1 17 12.8 10.7 13.5 7.5 20.4Z" fill="#ead6ff" stroke="#f8f1ff" stroke-width="1.1" stroke-linejoin="round"/></svg></span>`;
  document.body.append(layer);
  const point = layer.querySelector('.studio-cursor__point');
  const spark = layer.querySelector('.studio-cursor__spark');
  const paths = [...layer.querySelectorAll('.studio-cursor__trail path')];
  const gradient = layer.querySelector('linearGradient');
  const nativeTarget = 'input,textarea,[contenteditable="true"],[role="textbox"],video,audio,iframe,[disabled],[aria-disabled="true"]';
  const actionable = 'a[href],button,select,summary,[role="button"],[role="tab"]';
  // A soft, readable comet: 300 ms, capped at 92 CSS px (56 px over controls).
  const lifetime = 300;
  let x = 0, y = 0, frame = 0, lastMove = 0, active = false, action = false;
  let points = [];
  function allowed() { return fine.matches && !reduced.matches && !contrast.matches; }
  function clearTrail() { points = []; paths.forEach(path => path.setAttribute('d', '')); }
  function hide() {
    active = false;
    root.classList.remove('has-studio-cursor');
    layer.classList.remove('is-visible', 'is-down');
    cancelAnimationFrame(frame); frame = 0;
    clearTrail();
  }
  function host() {
    const dialogs = document.querySelectorAll('dialog[open]');
    const parent = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
    if (layer.parentNode !== parent) parent.append(layer);
  }
  function paint(now) {
    frame = 0;
    if (!active || !allowed() || document.hidden) { hide(); return; }
    points = points.filter(p => now - p.time < lifetime);
    const trail = [{x, y}];
    let distance = 0, previous = {x, y};
    const maxLength = action ? 56 : 92;
    for (let i = points.length - 1; i >= 0; i--) {
      const p = points[i], segment = Math.hypot(previous.x - p.x, previous.y - p.y);
      if (segment < .35) continue;
      const ratio = Math.min(1, (maxLength - distance) / segment);
      trail.push({x:previous.x + (p.x - previous.x) * ratio, y:previous.y + (p.y - previous.y) * ratio});
      distance += segment;
      if (distance >= maxLength) break;
      previous = p;
    }
    if (trail.length > 1 && distance > .5) {
      const end = trail[trail.length - 1];
      let d = `M${x.toFixed(1)} ${y.toFixed(1)}`;
      for (let i = 1; i < trail.length - 1; i++) {
        const p = trail[i], next = trail[i + 1];
        d += ` Q${p.x.toFixed(1)} ${p.y.toFixed(1)} ${((p.x + next.x)/2).toFixed(1)} ${((p.y + next.y)/2).toFixed(1)}`;
      }
      d += ` L${end.x.toFixed(1)} ${end.y.toFixed(1)}`;
      gradient.setAttribute('x1', String(x)); gradient.setAttribute('y1', String(y));
      gradient.setAttribute('x2', String(end.x)); gradient.setAttribute('y2', String(end.y));
      paths.forEach(path => path.setAttribute('d', d));
      layer.querySelector('.studio-cursor__trail').style.opacity = String(Math.max(0, 1 - (now - lastMove) / lifetime));
      spark.style.setProperty('--cursor-tilt', `${Math.max(-12, Math.min(12, (x - end.x) * .3))}deg`);
    } else {
      paths.forEach(path => path.setAttribute('d', ''));
      spark.style.setProperty('--cursor-tilt', '0deg');
    }
    if (points.length && now - lastMove < lifetime) frame = requestAnimationFrame(paint);
  }
  function move(event) {
    if (event.pointerType !== 'mouse' || !allowed() || !(event.target instanceof Element)) { hide(); return; }
    const target = event.target;
    if (target.closest(nativeTarget)) { hide(); return; }
    host();
    const now = performance.now();
    if (!active || Math.hypot(event.clientX - x, event.clientY - y) > 180) clearTrail();
    else points.push({x, y, time:lastMove});
    x = event.clientX; y = event.clientY; lastMove = now;
    active = true; action = Boolean(target.closest(actionable));
    // No easing on the point: clicks always land exactly where it points.
    point.style.transform = `translate3d(${x}px,${y}px,0)`;
    layer.classList.toggle('is-action', action);
    layer.classList.add('is-visible'); root.classList.add('has-studio-cursor');
    if (!frame) frame = requestAnimationFrame(paint);
  }
  document.addEventListener('pointermove', move, {passive:true});
  document.addEventListener('pointerover', move, {passive:true});
  document.addEventListener('pointerdown', event => {
    if (event.pointerType !== 'mouse') { hide(); return; }
    // Let native menus and media controls keep their own pointer behavior.
    if (event.target instanceof Element && event.target.closest('select')) { hide(); return; }
    if (active && event.button === 0) { layer.classList.add('is-down'); clearTrail(); }
  }, {passive:true});
  function release() { layer.classList.remove('is-down'); }
  document.addEventListener('pointerup', release, {passive:true});
  document.addEventListener('pointercancel', hide, {passive:true});
  document.addEventListener('pointerout', event => { if (!event.relatedTarget) hide(); }, {passive:true});
  document.addEventListener('keydown', hide);
  document.addEventListener('close', hide, true);
  document.addEventListener('cancel', hide, true);
  document.addEventListener('visibilitychange', () => { if (document.hidden) hide(); });
  window.addEventListener('blur', hide);
  window.addEventListener('pagehide', hide);
  window.addEventListener('scroll', hide, {passive:true});
  [fine, reduced, contrast].forEach(query => query.addEventListener('change', hide));
})();
