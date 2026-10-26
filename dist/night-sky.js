(() => {
  'use strict';
  const canvas = document.querySelector('.night-sky');
  if (!canvas) return;
  const context = canvas.getContext('2d', {alpha:true});
  if (!context) return;
  const section = canvas.parentElement;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const toggle = document.querySelector('#scene-motion-toggle');
  let width = 0, height = 0, stars = [], visible = false;
  let frame = 0, lastFrame = 0, elapsed = 0;

  function paused() { return reduced.matches || toggle?.getAttribute('aria-pressed') === 'true'; }
  function resize() {
    const rect = section.getBoundingClientRect();
    width = rect.width; height = rect.height;
    const ratio = Math.min(devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    // A stable seed keeps the sky fixed as the viewport changes.
    let seed = 2317;
    const random = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed / 4294967296;
    };
    const count = Math.max(110, Math.min(680, Math.round(width * height / 2300)));
    stars = Array.from({length:count}, () => {
      const x = random(), y = random(), size = random();
      const bright = size > .95, middle = size > .64;
      const radius = bright ? 1.45 + random() * .55 : middle ? .85 + random() * .5 : .48 + random() * .42;
      const alpha = bright ? .94 : middle ? .72 + random() * .25 : .48 + random() * .3;
      return {x,y,radius,alpha,
        phase:random() * Math.PI * 2,period:4.5 + random() * 5.5,
        color:random() < .17 ? '189,207,244' : '237,235,249',glow:bright};
    });
    draw();
    updateVisibility();
  }
  function draw() {
    context.clearRect(0, 0, width, height);
    for (const star of stars) {
      const x = star.x * width, y = star.y * height;
      // Vary brightness gently; the stars never blink out or move like particles.
      const phase = elapsed * Math.PI * 2 / star.period + star.phase;
      const brightness = .75 + Math.sin(phase) * .21 + Math.sin(phase * 1.73) * .04;
      const alpha = star.alpha * brightness;
      if (star.glow) {
        const halo = context.createRadialGradient(x,y,0,x,y,star.radius * 4);
        halo.addColorStop(0, `rgba(${star.color},${alpha * .38})`);
        halo.addColorStop(1, `rgba(${star.color},0)`);
        context.fillStyle = halo;
        context.beginPath(); context.arc(x,y,star.radius * 4,0,Math.PI * 2); context.fill();
      }
      context.fillStyle = `rgba(${star.color},${alpha})`;
      context.beginPath(); context.arc(x,y,star.radius,0,Math.PI * 2); context.fill();
    }
  }
  function animate(now) {
    if (now - lastFrame >= 50) {
      elapsed += Math.min(.1, (now - lastFrame) / 1000);
      lastFrame = now;
      draw();
    }
    frame = requestAnimationFrame(animate);
  }
  function sync() {
    cancelAnimationFrame(frame); frame = 0;
    const active = visible && !paused() && !document.hidden;
    canvas.dataset.motion = document.hidden ? 'hidden' : paused() ? 'paused' : visible ? 'twinkling' : 'offscreen';
    if (active) { lastFrame = performance.now(); frame = requestAnimationFrame(animate); }
  }
  function updateVisibility() {
    const rect = section.getBoundingClientRect();
    const next = rect.top < innerHeight && rect.bottom > 0;
    if (next !== visible) { visible = next; sync(); }
  }
  new ResizeObserver(resize).observe(section);
  // Also follows the browser's restored scroll position after a reload.
  window.addEventListener('scroll', updateVisibility, {passive:true});
  if (toggle) new MutationObserver(sync).observe(toggle, {attributes:true,attributeFilter:['aria-pressed']});
  reduced.addEventListener('change', sync);
  document.addEventListener('visibilitychange', sync);
  window.addEventListener('pagehide', () => { cancelAnimationFrame(frame); frame = 0; });
  window.addEventListener('pageshow', () => { updateVisibility(); sync(); });
  resize();
})();
