(() => {
  'use strict';
  window.PreludeAccess = {
    create(deps) {
      let state = null, busy = false, mode = 'activate';
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'access-trigger'; button.id = 'open-access'; button.disabled = true;
      button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-controls', 'access-dialog');
      button.innerHTML = '<span class="access-star" aria-hidden="true">✦</span><span>内测通行证</span>';
      const header = document.querySelector('.site-header');
      if (header) header.insertBefore(button, header.querySelector('.workspace-button'));
      else document.querySelector('.topbar-actions').prepend(button);
      const quotaLine = document.createElement('p'); quotaLine.className = 'access-quota'; quotaLine.hidden = true;
      quotaLine.setAttribute('aria-live', 'polite'); document.querySelector('#generation-cost').closest('p').after(quotaLine);
      const dialog = document.createElement('dialog'); dialog.id = 'access-dialog';
      dialog.setAttribute('aria-labelledby', 'access-title');
      dialog.innerHTML = `
        <button type="button" class="access-close" aria-label="关闭通行证">×</button>
        <div class="access-layout">
          <div class="access-atmosphere"><span class="access-eyebrow">PRELUDE / PRIVATE BETA</span><div><span class="access-script">A place for<br>your imagination</span><p>每一幕想象<br>都值得被珍藏</p></div><small>一张通行证，留住你的创作旅程</small></div>
          <div class="access-content"><span class="access-eyebrow">YOUR CREATIVE PASS</span><h2 id="access-title">让下一幕，从这里开始</h2><p class="access-intro">保留你的作品，下次回来继续创作</p>
            <div id="access-guest"><div class="access-tabs" role="tablist" aria-label="通行证操作"><button type="button" id="access-activate-tab" role="tab" aria-selected="true" aria-controls="access-form">首次使用</button><button type="button" id="access-login-tab" role="tab" aria-selected="false" aria-controls="access-form" tabindex="-1">已有通行证</button></div>
              <form id="access-form" role="tabpanel" aria-labelledby="access-activate-tab">
                <label id="access-invite-field">邀请码<input id="access-invitation" name="invitation" autocomplete="off" maxlength="200" placeholder="填写邀请人给你的邀请码" required></label>
                <label>通行名<input id="access-name" name="username" aria-label="通行名" aria-describedby="access-name-hint" autocomplete="username" minlength="2" maxlength="24" placeholder="例如 小雨的片场" required><small id="access-name-hint">2–24 个中文、字母、数字、下划线或短横线</small></label>
                <label>密码<div class="access-password"><input id="access-password" name="password" aria-label="密码" type="password" autocomplete="new-password" minlength="12" maxlength="128" placeholder="至少 12 个字符，试试一句好记的短语" required><button type="button" id="access-show-password" aria-label="显示密码" aria-pressed="false">显示</button></div></label>
                <p class="access-form-hint" id="access-form-hint">首次开通会将本浏览器已有的视频与参考图绑定到你的通行证</p>
                <p id="access-status" role="status" aria-live="polite"></p><button type="submit" class="access-primary" id="access-submit">开启我的创作旅程 <span aria-hidden="true">↗</span></button>
              </form>
            </div>
            <div id="access-member" hidden><p class="access-member-name"></p><div class="access-balance"><span>可生成时长<strong id="access-remaining">0 <small>秒</small></strong></span><span class="access-total" id="access-total"></span></div><p class="access-member-note">在同一服务地址，用通行名和密码登录，即可找回已绑定的视频与参考图</p><p class="access-member-note">提交时预留时长，失败后释放；删除作品不会返还额度。额度用完可联系邀请人</p><button type="button" class="access-primary" id="access-continue">继续我的创作 <span aria-hidden="true">↗</span></button><button type="button" class="access-logout" id="access-logout">退出通行证</button><p id="access-member-status" role="status" aria-live="polite"></p></div>
          </div>
        </div>`;
      document.body.append(dialog);
      const $ = selector => dialog.querySelector(selector);
      const form = $('#access-form'), password = $('#access-password');
      function setMode(value) {
        if (busy) return;
        mode = value;
        const activating = mode === 'activate';
        $('#access-invite-field').hidden = !activating; $('#access-invitation').required = activating;
        password.autocomplete = activating ? 'new-password' : 'current-password';
        $('#access-form-hint').textContent = activating ? '首次开通会将本浏览器已有的视频与参考图绑定到你的通行证' : '用原来的通行名与密码找回作品；未绑定的上传图需要重新选择';
        $('#access-submit').firstChild.textContent = activating ? '开启我的创作旅程 ' : '回到我的创作空间 ';
        ['activate', 'login'].forEach(key => { const tab = $(`#access-${key}-tab`); tab.setAttribute('aria-selected', String(key === mode)); tab.tabIndex = key === mode ? 0 : -1; });
        form.setAttribute('aria-labelledby', `access-${mode}-tab`); $('#access-status').textContent = '';
      }
      function open() {
        if (!state) return;
        $('#access-member-status').textContent = ''; $('#access-status').textContent = '';
        if (!dialog.open) dialog.showModal();
      }
      button.addEventListener('click', open);
      $('.access-close').addEventListener('click', () => { if (!busy) dialog.close(); });
      $('#access-continue').addEventListener('click', () => dialog.close());
      dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
      dialog.addEventListener('close', () => { password.value = ''; $('#access-invitation').value = ''; password.type = 'password'; $('#access-show-password').textContent = '显示'; $('#access-show-password').setAttribute('aria-pressed', 'false'); });
      ['activate', 'login'].forEach(value => $(`#access-${value}-tab`).addEventListener('click', () => setMode(value)));
      $('.access-tabs').addEventListener('keydown', event => {
        if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
          event.preventDefault(); setMode(event.key === 'Home' ? 'activate' : event.key === 'End' ? 'login' : mode === 'activate' ? 'login' : 'activate'); $(`#access-${mode}-tab`).focus();
        }
      });
      $('#access-show-password').addEventListener('click', () => { const show = password.type === 'password'; password.type = show ? 'text' : 'password'; $('#access-show-password').textContent = show ? '隐藏' : '显示'; $('#access-show-password').setAttribute('aria-pressed', String(show)); $('#access-show-password').setAttribute('aria-label', show ? '隐藏密码' : '显示密码'); });
      function lock(value) {
        busy = value;
        dialog.setAttribute('aria-busy', String(value));
        dialog.querySelectorAll('button,input').forEach(control => control.disabled = value);
      }
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (busy) return;
        if (deps.locked(mode)) { $('#access-status').textContent = '请先完成当前提交或核对原任务，再切换通行证'; return; }
        const payload = {name: $('#access-name').value.trim(), password: password.value, invitation: $('#access-invitation').value.trim()};
        lock(true); $('#access-status').textContent = mode === 'activate' ? '正在为你保留创作空间' : '正在找回你的创作空间';
        try {
          await deps.api(`/api/access/${mode}`, {method:'POST', headers:{'Content-Type':'application/json','X-Prelude-CSRF':deps.csrf()}, body:JSON.stringify(payload)});
          password.value = ''; deps.changed(mode);
        } catch (error) { $('#access-status').textContent = error.code ? error.message : '连接中断，请重新登录核对；邀请码如已使用，请切换到「已有通行证」'; }
        finally { lock(false); }
      });
      $('#access-logout').addEventListener('click', async () => {
        if (busy) return;
        if (deps.locked('logout')) { $('#access-member-status').textContent = '当前任务仍在进行或待核对，完成后再退出，方便继续查看进度'; return; }
        lock(true);
        try {
          await deps.api('/api/access/logout', {method:'POST', headers:{'Content-Type':'application/json','X-Prelude-CSRF':deps.csrf()}, body:'{}'});
          deps.changed('logout');
        } catch (error) { $('#access-member-status').textContent = error.code ? error.message : '退出结果待确认，请刷新页面核对'; }
        finally { lock(false); }
      });
      function update(value, seconds) {
        state = value || {invite_only:false,signed_in:false};
        button.disabled = false;
        const signedIn = state.signed_in, quota = state.quota;
        $('#access-guest').hidden = signedIn; $('#access-member').hidden = !signedIn;
        button.lastElementChild.textContent = signedIn ? `通行证 · ${quota.remaining_seconds} 秒` : '内测通行证';
        button.title = signedIn ? `${quota.name} · 查看内测额度与登录信息` : '使用邀请开通，或登录找回作品';
        quotaLine.hidden = !state.invite_only && !signedIn;
        if (signedIn) {
          $('.access-member-name').textContent = `${quota.name}，欢迎回来`;
          $('#access-remaining').replaceChildren(document.createTextNode(String(quota.remaining_seconds) + ' '));
          const unit = document.createElement('small'); unit.textContent = '秒'; $('#access-remaining').append(unit);
          $('#access-total').textContent = `已预留 / 使用 ${quota.used_seconds} 秒\n共 ${quota.total_seconds} 秒`;
          quotaLine.textContent = `内测剩余 ${quota.remaining_seconds} 秒 · 本次需 ${seconds} 秒`;
          quotaLine.classList.toggle('is-low', seconds > quota.remaining_seconds);
        } else { quotaLine.textContent = '邀请内测 · 生成前登录，作品会跟随通行证保留'; quotaLine.classList.remove('is-low'); }
        const localSpace = document.querySelector('.local-space > div');
        if (localSpace) { localSpace.firstChild.textContent = signedIn ? quota.name : '我的创作空间'; localSpace.querySelector('small').textContent = signedIn ? '作品已绑定通行证' : '邀请内测'; }
        document.querySelectorAll('.library-note').forEach(note => note.textContent = signedIn ? '视频与参考图已绑定通行证，可在同一服务地址登录找回' : '本浏览器已有作品会在首次开通通行证时绑定，之后可登录找回');
      }
      return {update, open, get state() { return state; }, require() { if (state?.invite_only && !state.signed_in) { open(); return false; } return Boolean(state); }};
    }
  };
})();
