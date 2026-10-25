(() => {
  'use strict';
  const activeStages = {SUBMITTING:0, PENDING:1, RUNNING:2, DOWNLOADING:3};
  const messages = {
    SUBMITTING:['正在送出你的想象','正在提交这段描述，镜头即将启程'],
    PENDING:['你的灵感，正在候场','镜头已进入队列，轮到后会自动开始生成'],
    RUNNING:['让想象，慢慢显影','光影与动作正在酝酿，给这一幕一点时间'],
    DOWNLOADING:['最后一束光，即将落定','画面已经生成，正在保存你的成片'],
    SUCCEEDED:['你的下一幕，即将揭晓','成片已保存，正在准备第一帧'],
    FAILED:['这一幕，暂未成形','保留这份灵感，稍后可以调整描述再尝试'],
    REJECTED:['镜头暂时未能启程','这次提交未能完成，描述已为你保留'],
    UNCERTAIN:['正在核对这次提交','提交结果尚未确认，会继续查询原请求'],
    UNAVAILABLE:['这一幕，暂时无法播放','原任务记录仍在，可稍后回来查看']
  };
  window.PreludeWaiting = {
    create(visual, player) {
      const progress = visual.querySelector('#job-progress');
      const clouds = progress.querySelector('.waiting-clouds');
      const title = progress.querySelector('#job-progress-title');
      const detail = progress.querySelector('#job-progress-detail');
      const elapsed = progress.querySelector('#job-elapsed');
      const eyebrow = progress.querySelector('#job-eyebrow');
      const stages = [...progress.querySelectorAll('[data-stage]')];
      const reduced = matchMedia('(prefers-reduced-motion: reduce)');
      const toggle = document.querySelector('#scene-motion-toggle');
      let job = null, mode = 'idle', interval = null, fade = null, visible = true, suspended = false;
      let motionRevision = 0;
      const isOpen = () => !visual.closest('dialog') || visual.closest('dialog').open;
      const isActive = () => job && Object.hasOwn(activeStages, job.status);
      function tick() {
        if (!isActive()) return;
        const seconds = Math.max(0, Math.floor(Date.now()/1000 - job.created_at));
        const minutes = Math.floor(seconds/60);
        elapsed.textContent = `已等待 ${String(minutes).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')} · 完成后会自动呈现`;
      }
      function syncActivity() {
        const onScreen = !suspended && !document.hidden && visible && isOpen();
        clearInterval(interval); interval = null;
        if (isActive() && onScreen) { tick(); interval = setInterval(tick, 1000); }
        const animate = onScreen && mode === 'waiting' && !reduced.matches && toggle?.getAttribute('aria-pressed') !== 'true';
        visual.classList.toggle('waiting-paused', !animate);
        const revision = ++motionRevision;
        if (!animate) { clouds.pause(); return; }
        if (!clouds.hasAttribute('src')) { clouds.src = 'assets/cloudscape-loop.mp4'; clouds.muted = true; clouds.load(); }
        const play = clouds.play();
        if (play) play.then(() => { if (revision !== motionRevision && visual.classList.contains('waiting-paused')) clouds.pause(); }).catch(() => {});
      }
      function clear() {
        clearTimeout(fade); clearInterval(interval); fade = interval = null;
        job = null; mode = 'idle'; progress.hidden = true;
        player.removeAttribute('inert'); player.removeAttribute('aria-hidden');
        delete visual.dataset.scene; visual.classList.remove('waiting-paused');
        motionRevision++; clouds.pause();
      }
      function reveal() {
        if (!job || job.status !== 'SUCCEEDED' || mode !== 'loading' || player.readyState < 2) return;
        mode = 'revealing'; visual.dataset.scene = 'revealing';
        player.removeAttribute('inert'); player.removeAttribute('aria-hidden');
        const expected = job.id;
        // The cloud layer leaves only after the actual first frame is decoded.
        fade = setTimeout(() => {
          if (job?.id !== expected || mode !== 'revealing') return;
          mode = 'ready'; visual.dataset.scene = 'ready'; progress.hidden = true;
          document.querySelector('#preview-status').textContent = '你的下一幕，已就绪 · 点击播放，开始欣赏';
          syncActivity();
        }, reduced.matches ? 0 : 900);
        syncActivity();
      }
      function render(next) {
        const same = job?.id === next.id;
        const unchanged = same && job.status === next.status;
        if (!same) { clearTimeout(fade); mode = 'idle'; }
        job = next;
        if (next.status === 'SUCCEEDED' && same && ['ready','revealing','media-error'].includes(mode)) return;
        const active = isActive(), uncertain = next.status === 'UNCERTAIN';
        mode = next.status === 'SUCCEEDED' ? 'loading' : active ? 'waiting' : 'failed';
        visual.dataset.scene = mode;
        player.hidden = next.status !== 'SUCCEEDED';
        player.setAttribute('inert',''); player.setAttribute('aria-hidden','true');
        progress.hidden = false;
        progress.classList.toggle('is-failed', !active && next.status !== 'SUCCEEDED');
        if (!unchanged) {
          const text = messages[next.status] || ['正在查询镜头状态','稍等片刻，工作室会自动更新'];
          title.textContent = text[0]; detail.textContent = next.error || text[1];
          eyebrow.textContent = active ? 'IMAGINATION IN MOTION' : next.status === 'SUCCEEDED' ? 'YOUR NEXT SCENE' : uncertain ? 'KEEPING YOUR SCENE SAFE' : 'A MOMENT BETWEEN SCENES';
          const step = next.status === 'SUCCEEDED' ? 4 : activeStages[next.status] ?? -1;
          stages.forEach((item,index) => {
            item.classList.toggle('is-done', index < step);
            item.classList.toggle('is-current', index === step);
            if(index === step) item.setAttribute('aria-current','step'); else item.removeAttribute('aria-current');
          });
        }
        if (active) tick();
        else elapsed.textContent = next.status === 'SUCCEEDED' ? '成片已保存 · 准备播放中' : uncertain ? '保留原请求，不会自动重新生成' : '你的描述与任务记录已保留';
        syncActivity();
        if (next.status === 'SUCCEEDED') reveal();
      }
      player.addEventListener('loadeddata', reveal);
      player.addEventListener('canplay', reveal);
      player.addEventListener('error', () => {
        if (!job || job.status !== 'SUCCEEDED') return;
        clearTimeout(fade); mode = 'media-error'; visual.dataset.scene = 'failed';
        progress.hidden = false; progress.classList.add('is-failed');
        title.textContent = '成片已保存，播放暂不可用';
        detail.textContent = '可以下载视频后欣赏，或稍后重新打开这条作品';
        elapsed.textContent = '无需重新生成'; syncActivity();
      });
      new IntersectionObserver(entries => { visible = entries[0].isIntersecting; syncActivity(); }).observe(visual);
      if (visual.closest('dialog')) new MutationObserver(syncActivity).observe(visual.closest('dialog'), {attributes:true, attributeFilter:['open']});
      if (toggle) new MutationObserver(syncActivity).observe(toggle, {attributes:true, attributeFilter:['aria-pressed']});
      reduced.addEventListener('change', syncActivity);
      document.addEventListener('visibilitychange', syncActivity);
      window.addEventListener('pagehide', () => { suspended = true; syncActivity(); });
      window.addEventListener('pageshow', () => { suspended = false; syncActivity(); });
      return {render, clear};
    }
  };
})();
