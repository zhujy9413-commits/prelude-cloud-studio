(() => {
  'use strict';
  const cameras = {
    still: ['固定镜头', '镜头固定，保持原有景别'],
    push: ['缓慢推进', '镜头极缓慢平稳向前推进'],
    pull: ['缓慢拉远', '镜头缓慢平稳后退，逐步展开环境'],
    follow: ['平稳跟随', '镜头平稳跟随运动主体，保持适当距离']
  };
  const paces = {
    gentle: ['轻柔舒缓', '动作轻柔舒缓，变化幅度克制'],
    natural: ['自然适中', '动作自然连贯，节奏适中'],
    lively: ['明快有力', '动作明快流畅，保持自然的运动节奏']
  };
  const ideas = {
    cloud: [['云层翻卷', '云层自然缓慢翻卷，边缘的光线柔和变化'], ['薄雾流动', '薄雾轻轻流动，云层缓慢舒展']],
    city: [['人物慢行', '人物缓慢向前行走，细雨持续落下，积水泛起轻微涟漪'], ['雨水涟漪', '细雨持续落下，积水泛起轻微涟漪']],
    lunar: [['宇航员迈步', '宇航员缓慢向前迈步，脚边少量尘埃轻轻扬起'], ['尘埃飘动', '月面上少量细小尘埃轻轻飘动']],
    custom: [['轻风拂动', '画面中可随风摆动的细节轻轻摇曳'], ['水面起伏', '水面自然起伏，细小波纹缓缓向外散开'], ['人物慢行', '画面中的人物缓慢向前行走，动作自然连贯']]
  };
  function create({ input, onApply }) {
    const entry = document.querySelector('#motion-guide-entry');
    const trigger = document.querySelector('#open-motion-guide');
    const undoRow = document.querySelector('#motion-undo-row');
    const dialog = document.createElement('dialog');
    dialog.id = 'motion-guide-dialog';
    dialog.setAttribute('aria-labelledby', 'motion-guide-title');
    dialog.innerHTML = `
      <div class="dialog-top"><span class="dialog-kicker">MOTION NOTES / 动作描述</span><button type="button" class="icon-button" data-motion-close aria-label="关闭动作引导"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></button></div>
      <div class="motion-layout">
        <aside class="motion-frame"><div class="motion-frame-heading"><span>从这一帧开始</span><span class="motion-frame-tag">参考首帧</span></div><div class="motion-image-wrap"><img id="motion-frame-image" alt="当前参考首帧"></div><div class="motion-frame-caption"><strong id="motion-frame-name"></strong><p>画面已经在这里<br>写下接下来发生的事</p><small>先聚焦一个主要动作，让变化更清楚</small></div></aside>
        <form id="motion-guide-form" class="motion-editor">
          <div class="motion-editor-body"><div class="motion-intro"><h2 id="motion-guide-title">让这一帧，慢慢流动</h2><p>写一点动作，再给镜头一个方向</p></div>
          <div class="motion-step"><label for="motion-action"><span class="motion-step-number">01</span>画面怎么动</label><textarea id="motion-action" rows="2" maxlength="240" required placeholder="例如：海浪涌向岸边，再缓缓退去" aria-describedby="motion-action-help"></textarea><p id="motion-action-help">只描述图中已有的主体，写清楚它怎么动</p><div class="motion-ideas" role="group" aria-label="动作灵感"></div></div>
          <fieldset class="motion-step"><legend><span class="motion-step-number">02</span>镜头怎么走</legend><div class="motion-choices" data-motion-group="camera" role="group" aria-label="镜头运动"></div></fieldset>
          <fieldset class="motion-step"><legend><span class="motion-step-number">03</span>想要什么节奏</legend><div class="motion-choices" data-motion-group="pace" role="group" aria-label="动作节奏"></div></fieldset>
          <div class="motion-output"><div><span>动作描述预览</span><span id="motion-output-count"></span></div><p id="motion-output-text">写下一个动作，完整描述会在这里慢慢成形</p></div>
          <p class="motion-write-note" id="motion-write-note"></p>
          </div><div class="motion-editor-actions"><button type="button" class="motion-cancel" data-motion-close>先不修改</button><button type="submit" class="motion-apply" id="apply-motion-guide"><span>写入描述</span><svg class="icon" aria-hidden="true"><use href="#i-arrow"/></svg></button></div>
        </form>
      </div>`;
    document.body.append(dialog);
    const $ = selector => dialog.querySelector(selector);
    const action = $('#motion-action'), apply = $('#apply-motion-guide');
    let frame = null, camera = 'still', pace = 'gentle', busy = false, undo = null;
    function clearUndo() { undo = null; undoRow.hidden = true; }
    function compose() {
      const movement = action.value.trim().replace(/^[，,。；;\s]+|[，,。；;\s]+$/g, '');
      return movement ? `延续首帧的主体与场景，${movement}，${paces[pace][1]}，${cameras[camera][1]}，保持原图主体的外观、光线与色调，连续单镜头，不切换场景` : '';
    }
    function render() {
      const prompt = compose();
      $('#motion-output-text').textContent = prompt || '写下一个动作，完整描述会在这里慢慢成形';
      $('#motion-output-count').textContent = prompt ? `${prompt.length} 字` : '';
      apply.disabled = busy || !frame || !prompt;
      apply.querySelector('span').textContent = input.value.trim() ? '替换当前描述' : '写入描述';
      $('#motion-write-note').textContent = input.value.trim() ? '将替换输入框里的文字，写入后仍可编辑或撤销' : '写入后仍可自由编辑，再确认时长并生成';
      for (const [group, selected] of [['camera',camera],['pace',pace]]) {
        $(`[data-motion-group="${group}"]`).querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.value === selected)));
      }
    }
    for (const [group, options] of [['camera', cameras], ['pace', paces]]) {
      for (const [value, [label]] of Object.entries(options)) {
        const button = document.createElement('button');
        button.type = 'button'; button.dataset.value = value; button.textContent = label;
        button.addEventListener('click', () => { if (group === 'camera') camera = value; else pace = value; render(); });
        $(`[data-motion-group="${group}"]`).append(button);
      }
    }
    trigger.addEventListener('click', () => {
      if (!frame || busy) return;
      render(); dialog.showModal(); action.focus({preventScroll:true});
    });
    dialog.querySelectorAll('[data-motion-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const r = dialog.getBoundingClientRect();
      if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
    });
    action.addEventListener('input', render);
    $('#motion-guide-form').addEventListener('submit', event => {
      event.preventDefault();
      const prompt = compose();
      if (!frame || busy || !prompt) return;
      const before = input.value;
      onApply(prompt);
      undo = before !== prompt ? {before, after:prompt, frame:frame.id} : null;
      undoRow.hidden = !undo;
      dialog.close(); input.focus({preventScroll:true});
    });
    undoRow.querySelector('button').addEventListener('click', () => {
      if (!undo || busy || input.value !== undo.after || frame?.id !== undo.frame) { clearUndo(); return; }
      const previous = undo.before; clearUndo(); onApply(previous); input.focus({preventScroll:true});
    });
    input.addEventListener('input', clearUndo);
    return {
      setFrame(value, preset) {
        if (frame?.id !== value?.id) {
          clearUndo(); action.value = ''; camera = 'still'; pace = 'gentle';
          $('.motion-ideas').replaceChildren();
          (ideas[preset] || ideas.custom).forEach(([label,text]) => {
            const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
            button.addEventListener('click', () => { action.value = text; render(); action.focus({preventScroll:true}); });
            $('.motion-ideas').append(button);
          });
        }
        frame = value; entry.hidden = !frame;
        action.placeholder = {cloud:'例如：云层缓慢翻卷，云边的光线柔和变化',city:'例如：人物向前慢行，细雨持续落下',lunar:'例如：宇航员缓慢迈步，脚边扬起少量月尘'}[preset] || '例如：海浪涌向岸边，再缓缓退去';
        if (frame) { $('#motion-frame-image').src = frame.image_url; $('#motion-frame-name').textContent = frame.name; }
        else if (dialog.open) dialog.close();
        render();
      },
      setBusy(value) { busy = value; trigger.disabled = busy; undoRow.querySelector('button').disabled = busy; render(); },
      clearUndo
    };
  }
  window.PreludeMotionGuide = {create};
})();
