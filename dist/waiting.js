(() => {
  'use strict';
  const activeStages = {SUBMITTING:0, PENDING:1, RUNNING:2, DOWNLOADING:3, PREPARING:3};
  const storageKey = 'prelude.playable-timings.v1';
  const clockText = seconds => {
    seconds = Math.max(0, Math.floor(seconds));
    return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
  };
  function readTimings() {
    try {
      const rows = JSON.parse(localStorage.getItem(storageKey) || '[]');
      return Array.isArray(rows) ? rows.filter(row => row && typeof row.request_id === 'string' &&
        Number.isFinite(row.started_at) && row.started_at > 0 &&
        (row.playable_at === null || (Number.isFinite(row.playable_at) && row.playable_at >= row.started_at))).slice(-40) : [];
    } catch { return []; }
  }
  const messages = {
    SUBMITTING:['正在送出你的想象','正在提交这段描述，镜头即将启程'],
    PENDING:['你的灵感，正在候场','镜头已进入队列，轮到后会自动开始生成'],
    RUNNING:['让想象，慢慢显影','光影与动作正在酝酿，给这一幕一点时间'],
    DOWNLOADING:['最后一束光，即将落定','画面已经生成，正在保存你的成片'],
    PREPARING:['让这一幕，流畅呈现','正在准备适合在线播放的成片'],
    SUCCEEDED:['你的下一幕，即将揭晓','成片已保存，正在准备第一帧'],
    FAILED:['这一幕，暂未成形','保留这份灵感，稍后可以调整描述再尝试'],
    REJECTED:['镜头暂时未能启程','这次提交未能完成，描述已为你保留'],
    UNCERTAIN:['为你保留这一幕','原任务结果需要核对，不会自动重新生成'],
    UNAVAILABLE:['这一幕，暂时无法播放','原任务记录仍在，可稍后回来查看']
  };
  window.PreludeWaiting = {
    storageKey,
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
      let timings = readTimings();
      function saveTiming(row) {
        // Keep timestamps only, with a bounded history. No prompts or media URLs.
        timings = [...readTimings().filter(item => item.request_id !== row.request_id), row].slice(-40);
        try { localStorage.setItem(storageKey, JSON.stringify(timings)); } catch {}
      }
      function begin(request) {
        if (!Number.isFinite(request.created_at) || request.created_at <= 0) return;
        if (!timings.some(item => item.request_id === request.request_id))
          saveTiming({request_id:request.request_id, started_at:request.created_at * 1000, playable_at:null});
      }
      const timing = () => timings.find(item => item.request_id === job?.request_id);
      const isLoading = () => job?.status === 'SUCCEEDED' && mode === 'loading';
      function readyText() {
        if (!['ready','revealing'].includes(mode)) return '';
        const row = timing();
        return '你的下一幕，已就绪 · ' + (row?.playable_at ?
          `从提交到可播放 ${clockText((row.playable_at-row.started_at)/1000)}` : '点击播放，开始欣赏');
      }
      const isOpen = () => !visual.closest('dialog') || visual.closest('dialog').open;
      const isActive = () => job && Object.hasOwn(activeStages, job.status);
      function tick() {
        if (!isActive() && !isLoading()) return;
        const row = timing();
        if (isLoading() && row?.playable_at) {
          elapsed.textContent = `首次可播放用时 ${clockText((row.playable_at-row.started_at)/1000)} · 正在载入成片`;
          return;
        }
        if (isLoading() && !row) {
          elapsed.textContent = '成片已保存 · 正在准备播放';
          return;
        }
        const started = row?.started_at ?? job.created_at * 1000;
        elapsed.textContent = `已等待 ${clockText((Date.now()-started)/1000)} · ${isLoading() ? '正在准备播放' : '就绪后会自动呈现'}`;
      }
      function syncActivity() {
        const onScreen = !suspended && !document.hidden && visible && isOpen();
        clearInterval(interval); interval = null;
        if ((isActive() || isLoading()) && onScreen) { tick(); interval = setInterval(tick, 1000); }
        const animate = onScreen && ['waiting','loading'].includes(mode) && !reduced.matches && toggle?.getAttribute('aria-pressed') !== 'true';
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
        if (!job || job.status !== 'SUCCEEDED' || mode !== 'loading' || player.readyState < 3 ||
            player.dataset.playbackReady === 'false' ||
            (player.dataset.playbackSource || player.getAttribute('src')) !== (job.playback_url || job.video_url)) return;
        const row = timing();
        if (row && row.playable_at === null) {
          const now = Date.now();
          // A changed system clock cannot produce a valid duration.
          if (now >= row.started_at) saveTiming({...row, playable_at:now});
        }
        mode = 'revealing'; visual.dataset.scene = 'revealing';
        player.removeAttribute('inert'); player.removeAttribute('aria-hidden');
        const expected = job.id;
        // Keep the cloud until the browser can advance beyond the first frame.
        fade = setTimeout(() => {
          if (job?.id !== expected || mode !== 'revealing') return;
          mode = 'ready'; visual.dataset.scene = 'ready'; progress.hidden = true;
          document.querySelector('#preview-status').textContent = readyText();
          syncActivity();
        }, reduced.matches ? 0 : 900);
        syncActivity();
      }
      function render(next) {
        const same = job?.id === next.id;
        const unchanged = same && job.status === next.status;
        if (!same) { clearTimeout(fade); mode = 'idle'; }
        job = next;
        if (next.status === 'SUCCEEDED' && same && ['ready','revealing'].includes(mode)) return;
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
        if(uncertain) detail.textContent = next.error || (next.recovery?.can_check ? '可以重新查询原任务，或联系邀请人核对' : '请将作品编号交给邀请人核对，记录已为你保留');
        if (active || isLoading()) tick();
        else elapsed.textContent = uncertain ? '保留原请求，不会自动重新生成' : '你的描述与任务记录已保留';
        syncActivity();
        if (next.status === 'SUCCEEDED') reveal();
      }
      player.addEventListener('loadeddata', reveal);
      player.addEventListener('canplay', reveal);
      player.addEventListener('playing', () => {
        if (mode === 'ready') document.querySelector('#preview-status').textContent = readyText();
      });
      function buffering() {
        if (mode === 'ready' && !player.paused && !player.ended)
          document.querySelector('#preview-status').textContent = '正在缓冲画面，片刻后继续';
      }
      player.addEventListener('waiting', buffering);
      player.addEventListener('stalled', buffering);
      function transfer({loaded,total,mode}) {
        if (!isLoading()) return;
        if(mode==='native'){detail.textContent='正在准备播放，原画质可直接下载';return;}
        const amount = total ? `${(loaded/1000000).toFixed(1)} / ${(total/1000000).toFixed(1)} MB` : `${(loaded/1000000).toFixed(1)} MB`;
        detail.textContent = total && loaded === total ? '成片已载入，正在准备播放' : `正在载入成片 ${amount} · 准备播放所需的画面`;
      }
      function mediaError(message) {
        if(typeof message!=='string'&&player.dataset.playbackManaged==='true')return;
        if (!job || job.status !== 'SUCCEEDED') return;
        clearTimeout(fade); mode = 'media-error'; visual.dataset.scene = 'failed';
        progress.hidden = false; progress.classList.add('is-failed');
        title.textContent = '成片已保存，播放暂不可用';
        detail.textContent = typeof message === 'string' ? message : '可以下载视频后欣赏，或稍后重新打开这条作品';
        elapsed.textContent = '无需重新生成'; syncActivity();
      }
      player.addEventListener('error',mediaError);
      new IntersectionObserver(entries => { visible = entries[0].isIntersecting; syncActivity(); }).observe(visual);
      if (visual.closest('dialog')) new MutationObserver(syncActivity).observe(visual.closest('dialog'), {attributes:true, attributeFilter:['open']});
      if (toggle) new MutationObserver(syncActivity).observe(toggle, {attributes:true, attributeFilter:['aria-pressed']});
      reduced.addEventListener('change', syncActivity);
      document.addEventListener('visibilitychange', syncActivity);
      window.addEventListener('pagehide', () => { suspended = true; syncActivity(); });
      window.addEventListener('pageshow', () => { suspended = false; syncActivity(); });
      function reload(){
        if(!job||job.status!=='SUCCEEDED')return;
        clearTimeout(fade);mode='loading';visual.dataset.scene='loading';progress.hidden=false;
        progress.classList.remove('is-failed');title.textContent='正在准备你的成片';
        detail.textContent='正在完成载入，作品与原画质均已保存';syncActivity();
      }
      return {render, clear, begin, readyText, transfer, mediaError, playable:reveal, reload};
    }
  };
})();
