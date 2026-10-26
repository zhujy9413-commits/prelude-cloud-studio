(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const presets = {
    cloud: { title: '暮色云海', image: 'assets/cloudscape.webp', prompt: '落日余晖中，镜头缓缓穿过蓝紫色云海，云层边缘泛起温暖的金粉色光芒，向远处发亮的地平线缓慢推进，真实的光影与电影质感。' },
    city: { title: '雨夜东京', image: 'assets/city.webp', prompt: '东京的雨夜，一位撑着透明雨伞的人走过安静的街道，红蓝霓虹倒映在积水中，镜头从背后缓慢跟随，雨丝清晰可见，写实电影质感。' },
    lunar: { title: '月面漫游', image: 'assets/lunar.webp', prompt: '一位宇航员独自行走在辽阔的月球表面，远处蓝色的地球从地平线升起，镜头以广角缓慢后退，展现宇宙的尺度，庄严、安静的科幻电影氛围。' }
  };
  const workspace = document.body.dataset.page === 'workspace';
  const input = $('#prompt'), form = $('#creation-form'), status = $('#form-status');
  const submit = $('.generate-button'), preview = $(workspace ? '#workspace-preview' : '#preview-dialog'), library = $(workspace ? '#works' : '#library-dialog');
  const player = $('#preview-video'), durationInput = $('#duration'), duration = window.PreludeDuration;
  const waiting = window.PreludeWaiting.create($('.preview-visual'), player);
  const draftKey = 'prelude.cloud.draft.v1', libraryKey = 'prelude.cloud.plans.v1', pendingKey = 'prelude.cloud.request.v1';
  const terminal = new Set(['SUCCEEDED', 'FAILED', 'REJECTED', 'UNCERTAIN', 'UNAVAILABLE']);
  const labels = { SUBMITTING:'正在提交镜头', PENDING:'镜头正在排队', RUNNING:'正在生成你的下一幕', DOWNLOADING:'正在保存你的视频', SUCCEEDED:'视频已生成', FAILED:'这次未能完成', REJECTED:'这次未能提交', UNCERTAIN:'正在等待任务核对', UNAVAILABLE:'视频暂不可用' };
  const promptPlaceholder = input.placeholder, promptHint = $('.prompt-meta > span').textContent;
  let referenceKey = null, motionGuide = null;
  let uploadedReference = null, uploading = false, supportsUpload = false;
  let selected = 'cloud', currentPlan = null, currentJob = null, savedCurrentId = null;
  let csrf = '', ready = false, posting = false, syncing = false, jobs = [], deletedJobs = [], timer = null, disconnected = false;
  let collection = null, supportsReference = false;
  let workspaceInitialized = false;
  let pendingRequest = readJSON(pendingKey, null);
  if (!pendingRequest || typeof pendingRequest.request_id !== 'string' || typeof pendingRequest.prompt !== 'string') pendingRequest = null;
  function readJSON(key, fallback) { try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; } }
  function writeJSON(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } }
  function clearPending() { pendingRequest = null; try { localStorage.removeItem(pendingKey); } catch {} }
  function isPreset(key) { return Object.hasOwn(presets, key); }
  function activeJob() { return jobs.find(j => !terminal.has(j.status) || j.status === 'UNCERTAIN'); }
  function titleFrom(prompt) { return prompt.length > 18 ? prompt.slice(0, 18) + '…' : prompt; }
  function effectiveDuration() { return duration.parse(durationInput.value) ?? duration.recommend(input.value).seconds; }
  function updateDuration() {
    const recommendation = duration.recommend(input.value), hasPrompt = Boolean(input.value.trim());
    const apply = $('#apply-duration');
    apply.disabled = !hasPrompt;
    apply.textContent = hasPrompt ? `建议 ${recommendation.seconds} 秒 · 采用` : '填写描述后推荐时长';
    apply.title = recommendation.reason + ' · 仅供参考，可自行填写';
    $('#duration-help > span').textContent = '可填写 2–15 秒';
    durationInput.setCustomValidity(durationInput.value !== '' && duration.parse(durationInput.value) === null ? '请填写 2 到 15 之间的整数秒数' : '');
    updateCost();
  }
  function updateCount() { $('#prompt-count').textContent = `${input.value.length} / 1200`; updateDuration(); }
  function hasReference() { return Boolean(referenceKey || uploadedReference); }
  function isUpload(value) { return value && typeof value.id==='string' && /^[a-f0-9-]{36}$/.test(value.id) && typeof value.name==='string' && /^\d{1,4}:\d{1,4}$/.test(value.aspect); }
  function saveDraft() { writeJSON(draftKey, { reference_preset:referenceKey, reference_upload:uploadedReference, prompt: input.value, aspect: $('#aspect').value, duration: $('#duration').value, preset: selected }); }
  function updateCost() {
    if (!durationInput.validity.valid) { $('#generation-cost').textContent = '请填写 2–15 秒的整数时长'; return; }
    const seconds = effectiveDuration(), automatic = durationInput.value === '';
    $('#generation-cost').textContent = `${hasReference()?'首帧生视频 · ':'文生视频 · '}720P · ${automatic ? (input.value.trim() ? '按建议 ' : '默认 ') : ''}${seconds} 秒标价 ¥${(seconds * (hasReference() ? .3 : .6)).toFixed(2)} · 可用免费额度优先抵扣`;
  }
  function setSelected(key) { if (!isPreset(key)) return; selected = key; $$('.prompt-suggestions [data-preset]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.preset === key))); }
  function focusComposer() { $('#create').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' }); input.focus({ preventScroll: true }); }
  function setReference(key, upload = null) {
    referenceKey=isPreset(key)?key:null;
    uploadedReference=!referenceKey&&isUpload(upload)?{id:upload.id,name:upload.name,aspect:upload.aspect,width:upload.width,height:upload.height,image_url:`/api/references/${upload.id}/image`}:null;
    const frame=uploadedReference||(referenceKey?{name:presets[referenceKey].title,image_url:presets[referenceKey].image,aspect:'16:9'}:null);
    $('#reference-panel').hidden=!frame;$('#generation-mode').hidden=Boolean(frame);
    $('#generation-mode').textContent=frame?'首帧生视频 · 使用已选图片引导画面，后续动作仍可能出现变化':'文生视频 · 根据文字重新构图，不使用示例图片';
    const aspect=$('#aspect');aspect.querySelector('[data-upload-aspect]')?.remove();
    aspect.disabled=Boolean(frame);aspect.title=frame?'画幅跟随参考首帧，移除首帧后可修改':'';
    if(frame){
      if(![...aspect.options].some(option=>option.value===frame.aspect)){const option=new Option(`${frame.aspect} · 原图`,frame.aspect);option.dataset.uploadAspect='';aspect.add(option);}
      aspect.value=frame.aspect;$('#reference-image').src=frame.image_url;$('#reference-image').alt=`${frame.name} 参考首帧`;
      $('#reference-title').textContent=`${uploadedReference?'上传首帧':'参考首帧'} · ${frame.name}`;
      $('#reference-description').textContent=uploadedReference?`${frame.width} × ${frame.height} · 点击图片预览首帧`:'此图将作为视频首帧 · 保留横屏构图与光影基调';
    }else if(!['16:9','9:16','1:1'].includes(aspect.value))aspect.value='16:9';
    input.placeholder=frame?'写下希望发生的动作，或试试下方的「帮我写动作」':promptPlaceholder;
    $('.prompt-meta > span').textContent=frame?'描述动作、镜头与节奏':promptHint;
    motionGuide?.setFrame(frame?{...frame,id:uploadedReference?.id||referenceKey}:null,referenceKey);
    $('#upload-reference span').textContent=uploadedReference?'更换参考图':'上传参考图';
    if(uploadedReference)$$('.prompt-suggestions [data-preset]').forEach(button=>button.setAttribute('aria-pressed','false'));
    if(workspace)$('.model-summary small').textContent=frame?'单镜头 · 首帧生视频':'单镜头 · 文生视频';
    if(workspace && !currentJob && !$('#workspace-welcome').hidden){
      $('#preview-image').src=frame?.image_url||presets.cloud.image;
      $('#preview-image').alt=frame?`${frame.name} 参考首帧`:'暮色云海灵感画面';
      $('#workspace-welcome h2').textContent=frame?'让这一帧，流动起来':'下一幕，等你来导演';
      $('#workspace-welcome p').textContent=frame?'From this frame':'Your next masterpiece';
      $('#workspace-welcome small').textContent=frame?'此图将作为首帧 · 当前尚未生成视频':'在左侧写下一句话，让画面从想象中走来';
      $('#preview-title').textContent=frame?`参考首帧 · ${frame.name}`:'画面预览';
      $('#preview-spec').textContent=frame?`${frame.aspect} · 等待生成`:'等待你的下一幕';
    }
    updateCost();
  }
  const motionPrompts={
    cloud:'延续首帧的蓝紫色暮色云海与金色夕阳，云层自然缓慢翻卷，镜头平稳向远处的地平线推进，保持原图的构图、色调与写实电影光影，连续单镜头，不切换场景',
    city:'延续首帧中的雨夜街道、透明雨伞和深色长外套人物，人物缓慢向前行走，镜头从背后平稳跟随并保持原有景别与距离，细密雨丝持续落下，积水泛起轻微涟漪，保留原图暗部层次、暖色灯笼与冷色霓虹的对比，连续单镜头，不切换场景',
    lunar:'延续首帧中的月面、宇航员和远处地球，宇航员缓慢向前迈步，尘埃轻微扬起，镜头以广角平稳缓慢后退，保持原图的地平线、光线方向与科幻电影色调，连续单镜头，不切换场景'
  };
  motionGuide=window.PreludeMotionGuide.create({input,onApply:prompt=>{
    input.value=prompt;updateCount();saveDraft();
    if(!activeJob()&&!pendingRequest)status.textContent='描述已更新，确认时长后即可生成';
  }});
  function fillPreset(key, scroll = true) { if (!isPreset(key)) return; motionGuide.clearUndo();setSelected(key);setReference(key); input.value = motionPrompts[key]; updateCount(); saveDraft(); if (!activeJob()) status.textContent = '已附上参考首帧，可以继续调整动作描述'; if (scroll) focusComposer(); }
  function setBusy() {
    const job = activeJob();
    submit.disabled = uploading || posting || Boolean(job) || !ready;
    form.setAttribute('aria-busy', String(uploading || posting || Boolean(job)));
    $('#generate-label').textContent = uploading ? '正在准备首帧' : posting ? '正在提交' : job ? (job.status === 'UNCERTAIN' ? '等待任务核对' : '正在生成视频') : pendingRequest ? '核对上次提交' : '生成我的视频';
    const referenceBusy=uploading||posting||Boolean(pendingRequest);
    motionGuide?.setBusy(referenceBusy);
    $('#use-preview').disabled=referenceBusy;
    if(workspace)$('#detail-reuse').disabled=referenceBusy;
    $$('#upload-reference,#remove-reference,[data-preset],[data-new-scene]').forEach(button=>button.disabled=referenceBusy);
    if (workspace) {
      const connection = $('#workspace-connection'); connection.classList.toggle('is-offline', disconnected || !ready);
      connection.replaceChildren(document.createElement('i'), document.createTextNode(disconnected ? '工作室连接中断' : ready ? '工作室已就绪' : '正在连接工作室'));
    }
    if (job) {
      status.replaceChildren(document.createTextNode((labels[job.status] || '正在处理') + ' · '));
      const link = document.createElement('a'); link.href = '#'; link.textContent = '查看进度';
      link.addEventListener('click', e => { e.preventDefault(); showJob(job); }); status.append(link);
    }
  }
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, { credentials:'same-origin', cache:'no-store', ...options, signal:controller.signal });
      const body = await response.json();
      if (!response.ok) { const error = new Error(body.error || '服务暂不可用'); error.code = body.code; throw error; }
      return body;
    } finally { clearTimeout(timeout); }
  }
  async function uploadImage(file) {
    if(uploading||posting||pendingRequest)return;
    const type=file.type==='image/jpg'?'image/jpeg':file.type;
    if(!['image/jpeg','image/png','image/webp'].includes(type)){status.textContent='请选择 JPG、PNG 或 WebP 图片';return;}
    if(!file.size||file.size>10*1024*1024){status.textContent='图片请控制在 10 MB 以内';return;}
    if(!csrf)await sync();
    if(!csrf||!supportsUpload){status.textContent='图片上传暂不可用，请确认本地工作室已更新并启动';return;}
    uploading=true;setBusy();status.textContent='正在准备你的参考首帧';
    try{
      const uploaded=await api('/api/references',{method:'POST',headers:{'Content-Type':type,'X-Prelude-CSRF':csrf,'X-Prelude-File-Name':encodeURIComponent(file.name)},body:file});
      setReference(null,uploaded);saveDraft();status.textContent='首帧已准备好，写下你希望发生的动作';
    }catch(error){status.textContent=error.code?error.message:'图片上传中断，原有参考图已保留，请重新选择';}
    finally{uploading=false;$('#reference-file').value='';setBusy();}
  }
  $('#upload-reference').addEventListener('click',()=>$('#reference-file').click());
  $('#reference-file').addEventListener('change',event=>{if(event.target.files[0])uploadImage(event.target.files[0]);});
  $('#preview-reference').addEventListener('click',()=>{
    $('#reference-full-image').src=$('#reference-image').getAttribute('src');$('#reference-full-title').textContent=$('#reference-title').textContent;
    $('#reference-dialog').showModal();
  });
  form.addEventListener('dragover',event=>{if([...event.dataTransfer.types].includes('Files')){event.preventDefault();if(!uploading)form.classList.add('is-dragging');}});
  form.addEventListener('dragleave',event=>{if(!form.contains(event.relatedTarget))form.classList.remove('is-dragging');});
  form.addEventListener('drop',event=>{event.preventDefault();form.classList.remove('is-dragging');if(event.dataTransfer.files.length!==1){status.textContent='一次选择一张参考图就好';return;}uploadImage(event.dataTransfer.files[0]);});
  function openPreview() { if (workspace) { preview.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'}); return; } if (library.open) library.close(); if (!preview.open) preview.showModal(); }
  function resetPlayer() { collection?.updatePreview(null); waiting.clear(); player.pause(); player.removeAttribute('src'); player.load(); player.hidden = true; }
  function showPreview(plan, savedId = null) {
    currentJob = null; resetPlayer();
    if (workspace) { $('#workspace-welcome').hidden=true; $('#workspace-preview-actions').hidden=false; $('#preview-prompt').hidden=false; $('#rename-work').hidden=true; }
    const key = isPreset(plan.preset) ? plan.preset : 'cloud';
    currentPlan = { ...plan, preset:key, reference_preset:key, kind:'concept-still' }; savedCurrentId = savedId;
    $('.preview-visual').classList.remove('is-video');
    $('#preview-image').hidden = false; $('#preview-image').src = presets[key].image; $('#preview-image').alt = `${presets[key].title} AI 概念静帧`;
    $('#job-progress').hidden = true; $('#preview-badge').hidden = false; $('#preview-badge').textContent = 'AI 概念静帧 · 非视频';
    $('#preview-kicker').textContent = 'VISUAL REFERENCE / 画面参考';
    $('#preview-title').textContent = plan.title || presets[key].title;
    $('#preview-spec').textContent = `${plan.aspect} · ${plan.duration} 秒 · 灵感参数`;
    $('#preview-prompt').textContent = plan.prompt; $('#preview-status').textContent = '';
    $('#preview-notice').textContent = '这是参考静帧，点击「按此画面生成」会把此图作为视频首帧；它会引导构图与光影，后续动作仍可能出现变化';
    $('#save-plan').hidden = false; $('#save-plan span').textContent = savedId ? '已保存镜头方案' : '保存镜头方案'; $('#save-plan').disabled = Boolean(savedId);
    $('#download-video').hidden = true; $('#download-video').removeAttribute('href'); $('#check-task').hidden = true;
    $('#use-preview-label').textContent='按此画面生成';
    openPreview();
  }
  function renderJob(job) {
    currentJob = job; currentPlan = { prompt:job.prompt, aspect:job.aspect, duration:String(job.duration), preset:selected, kind:'generated-video', reference_preset:job.reference_preset || null, reference_id:job.reference_id || null };
    writeJSON('prelude.workspace.selected.v1',job.id);
    if (workspace) { $('#workspace-welcome').hidden=true; $('#workspace-preview-actions').hidden=false; $('#preview-prompt').hidden=false; $('#rename-work').hidden=false; $$('#library-items [data-job-id]').forEach(card=>card.classList.toggle('is-selected',card.dataset.jobId===job.id)); }
    $('.preview-visual').classList.add('is-video'); $('#preview-image').hidden = true; $('#save-plan').hidden = true;
    $('#preview-kicker').textContent = workspace ? '02 / YOUR NEXT SCENE' : 'YOUR NEXT SCENE / 你的下一幕';
    $('#preview-title').textContent = job.title || (job.status === 'SUCCEEDED' ? titleFrom(job.prompt) : (labels[job.status] || '正在处理'));
    $('#preview-spec').textContent = `${job.aspect} · ${job.duration} 秒 · 720P · ${job.reference_preset||job.reference_id?'首帧生视频':'文生视频'}`;
    $('#preview-prompt').textContent = job.prompt;
    const completed = job.status === 'SUCCEEDED', failed = terminal.has(job.status) && !completed;
    $('#preview-badge').hidden = !completed; $('#preview-badge').textContent = 'AI 生成视频';
    if (completed && player.getAttribute('src') !== job.video_url) { player.src = job.video_url; player.load(); }
    if (!completed && player.hasAttribute('src')) resetPlayer();
    waiting.render(job); collection?.updatePreview(job);
    $('#use-preview-label').textContent='继续调整';
    $('#download-video').hidden = !completed;
    if (completed) $('#download-video').href = job.video_url + '?download=1'; else $('#download-video').removeAttribute('href');
    $('#check-task').hidden = completed || ['FAILED','REJECTED','UNAVAILABLE'].includes(job.status);
    $('#preview-notice').textContent = completed ? '视频已保存在这台电脑的工作室中，可以播放、下载，或使用这句描述创作下一幕' : failed ? '原任务已保留，不会自动重新生成或重复扣费' : '关闭这个窗口不会取消生成，刷新页面也可以恢复进度';
    $('#preview-status').textContent = disconnected ? '连接暂时中断，正在重新查询原任务，不会重新提交' : '';
  }
  function showJob(job) { currentPlan = null; resetPlayer(); renderJob(job); openPreview(); }
  function showSubmission(request, state = 'SUBMITTING', message = '') {
    currentJob = null; resetPlayer();
    currentPlan = {prompt:request.prompt,aspect:request.aspect,duration:String(request.duration),preset:selected,kind:'generated-video',reference_preset:request.reference_preset||null,reference_id:request.reference_id||null};
    if(workspace) { $('#workspace-welcome').hidden=true; $('#workspace-preview-actions').hidden=false; $('#rename-work').hidden=true; }
    $('.preview-visual').classList.add('is-video'); $('#preview-image').hidden=true; $('#preview-badge').hidden=true;
    $('#preview-kicker').textContent='YOUR NEXT SCENE / 你的下一幕';
    $('#preview-title').textContent=titleFrom(request.prompt); $('#preview-spec').textContent=`${request.aspect} · ${request.duration} 秒 · 720P`;
    $('#preview-prompt').hidden=false; $('#preview-prompt').textContent=request.prompt;
    $('#save-plan').hidden=true; $('#download-video').hidden=true; $('#check-task').hidden=state==='SUBMITTING';
    $('#use-preview-label').textContent='继续调整';
    $('#preview-status').textContent=''; $('#preview-notice').textContent='你的描述会保留在工作室中，可以稍后回来查看进度';
    waiting.render({id:request.request_id,status:state,created_at:request.created_at || Date.now()/1000,error:message});
    openPreview();
  }
  function readPlans() { const raw = readJSON(libraryKey, []); return Array.isArray(raw) ? raw.filter(p => p && typeof p.id === 'string' && typeof p.prompt === 'string' && isPreset(p.preset) && ['16:9','9:16','1:1'].includes(p.aspect) && duration.parse(p.duration) !== null).slice(0,40) : []; }
  function icon(name) { const svg = document.createElementNS('http://www.w3.org/2000/svg','svg'); svg.setAttribute('class','icon'); svg.setAttribute('aria-hidden','true'); const use = document.createElementNS(svg.namespaceURI,'use'); use.setAttribute('href','#i-'+name); svg.append(use); return svg; }
  function renderLibrary() {
    if (workspace) { renderWorkspaceLibrary(); return; }
    const plans = readPlans().filter(plan=>!plan.deleted_at), items = $('#library-items'); items.replaceChildren(); $('#library-empty').hidden = jobs.length + plans.length > 0;
    [...jobs.map(job => ({job})), ...plans.map(plan => ({plan}))].forEach(({job,plan}) => {
      const item = job || plan, button = document.createElement('button'); button.type = 'button'; button.className = 'library-item';
      let cover;
      if (job) { cover = document.createElement('div'); cover.className = 'library-video-cover'; cover.append(icon(job.status === 'SUCCEEDED' ? 'play':'film')); }
      else { cover = document.createElement('img'); cover.src = presets[plan.preset].image; cover.alt = presets[plan.preset].title; }
      const detail = document.createElement('div'), title = document.createElement('h3'), text = document.createElement('p'), meta = document.createElement('small');
      title.textContent = job ? job.title || titleFrom(job.prompt) : plan.title || presets[plan.preset].title; text.textContent = item.prompt;
      meta.textContent = `${item.aspect} · ${item.duration} 秒 · ${job ? labels[job.status] || '处理中' : '镜头方案'}`;
      detail.append(title,text,meta); button.append(cover,detail,icon('diagonal'));
      button.addEventListener('click', () => job ? showJob(job) : showPreview(plan,plan.id)); items.append(button);
    });
  }
  function renderWorkspaceLibrary() { collection?.render(); }
  function schedulePoll() { clearTimeout(timer); if (activeJob() || pendingRequest || disconnected) timer = setTimeout(() => sync(), 10000); }
  async function sync(initial = false) {
    if (syncing) return; syncing = true;let referenceMessage='';
    try {
      const data = await api('/api/bootstrap'); csrf = data.csrf; ready = data.ready; supportsReference = data.reference_supported === true; supportsUpload=data.upload_supported===true;jobs = data.jobs; deletedJobs = data.deleted_jobs || []; disconnected = false;
      if(initial&&uploadedReference&&supportsUpload){
        try{setReference(null,await api(`/api/references/${uploadedReference.id}`));}
        catch(error){if(error.code==='reference_not_found'){setReference(null);saveDraft();referenceMessage='参考图已不可用，请重新上传后再生成';}else throw error;}
      }
      if (pendingRequest && jobs.some(j => j.request_id === pendingRequest.request_id)) clearPending();
      if(workspace && !workspaceInitialized) {
        const saved=readJSON('prelude.workspace.selected.v1','initial');
        const choice=activeJob()||(saved===null?null:jobs.find(j=>j.id===saved)||jobs.find(j=>j.status==='SUCCEEDED')||jobs[0]);
        if(choice) renderJob(choice); workspaceInitialized=true;
      }
      if (initial && !workspace && activeJob()) showJob(activeJob());
      if (currentJob) { const fresh = jobs.find(j => j.id === currentJob.id); if (fresh) renderJob(fresh); }
      if (workspace || library.open) renderLibrary();
      $('#library-status').textContent = '';
      if (!ready) status.textContent = '服务端还未配置百炼密钥';
      else if (pendingRequest) status.textContent = '上次提交结果待确认，再点按钮将核对同一请求';
      else if (!activeJob() && initial) status.textContent = referenceMessage;
      else if (!activeJob() && status.querySelector('a')) status.textContent = '视频任务已更新，可在「我的作品」中查看';
    } catch {
      disconnected = true;
      const message = location.protocol === 'file:' ? '真实生成需要启动本地工作室，请运行 python3 server.py 后访问 http://127.0.0.1:4173' : '暂时无法连接工作室，正在重新连接；已有任务不会重复提交';
      status.textContent = message; $('#library-status').textContent = message;
      if (currentJob) $('#preview-status').textContent = message;
    } finally { syncing = false; setBusy(); schedulePoll(); }
  }
  if(workspace) collection=window.PreludeCollection.create({
    getJobs:()=>jobs,getDeleted:()=>deletedJobs,getPlans:readPlans,savePlans:plans=>writeJSON(libraryKey,plans),
    api,csrf:()=>csrf,refresh:sync,labels,icon,titleFrom,showJob,showPlan:showPreview,
    planImage:key=>presets[isPreset(key)?key:'cloud'].image,reuse:plan=>reuseDescription(plan),
    updateJob:updated=>{
      jobs=jobs.filter(job=>job.id!==updated.id);deletedJobs=deletedJobs.filter(job=>job.id!==updated.id);
      (updated.deleted_at?deletedJobs:jobs).unshift(updated);
      if(currentJob?.id===updated.id&&!updated.deleted_at)renderJob(updated);
    },
    onRemoved:(kind,id)=>{
      if((kind==='job'&&currentJob?.id===id)||(kind==='plan'&&savedCurrentId===id)){
        currentJob=null;currentPlan=null;savedCurrentId=null;resetPlayer();
        writeJSON('prelude.workspace.selected.v1',null);
        $('#preview-image').hidden=false;$('#preview-image').src=presets.cloud.image;$('.preview-visual').classList.remove('is-video');
        $('#workspace-welcome').hidden=false;$('#workspace-preview-actions').hidden=true;$('#rename-work').hidden=true;$('#preview-prompt').hidden=true;$('#preview-badge').hidden=true;
        $('#preview-title').textContent='画面预览';$('#preview-spec').textContent='等待你的下一幕';$('#preview-notice').textContent='这条作品已移入最近删除，可以随时恢复';$('#preview-status').textContent='';
      }
    }
  });
  const draft = readJSON(draftKey,null);
  if (draft && typeof draft === 'object') {
    input.value = typeof draft.prompt === 'string' ? draft.prompt.slice(0,1200) : '';
    if (['16:9','9:16','1:1'].includes(draft.aspect)) $('#aspect').value = draft.aspect;
    if (draft.duration === '' || duration.parse(draft.duration) !== null) durationInput.value = draft.duration;
    if (isPreset(draft.preset)) setSelected(draft.preset);
    setReference(draft.reference_preset,draft.reference_upload);
  }
  updateCount(); updateCost();
  $('#remove-reference').addEventListener('click',()=>{const prior=referenceKey;setReference(null);if(prior&&input.value===motionPrompts[prior])input.value=presets[prior].prompt;updateCount();saveDraft();});
  input.addEventListener('input', () => { updateCount(); saveDraft(); if (!activeJob() && !pendingRequest) status.textContent = ''; });
  durationInput.addEventListener('input', () => { updateDuration(); saveDraft(); });
  $('#apply-duration').addEventListener('click', () => { durationInput.value = duration.recommend(input.value).seconds; updateDuration(); saveDraft(); });
  $$('.generation-settings select').forEach(s => s.addEventListener('change', () => { saveDraft(); updateCost(); }));
  $$('[data-preset]').forEach(b => b.addEventListener('click', () => fillPreset(b.dataset.preset,!b.closest('.creation-pass'))));
  $$('[data-preview]').forEach(b => b.addEventListener('click', () => { const key = b.dataset.preview; showPreview({preset:key,title:presets[key].title,prompt:motionPrompts[key],aspect:'16:9',duration:'5',kind:'concept-still'}); }));
  $$('[data-open-library]').forEach(b => b.addEventListener('click', () => { if(!workspace){location.href='workspace.html#works';return;} library.scrollIntoView({behavior:'smooth'}); sync(); }));
  $$('[data-close-dialog]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));
  [preview,library,$('#reference-dialog')].filter(element=>element.tagName==='DIALOG').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const r=dialog.getBoundingClientRect(); if (event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) dialog.close(); } }));
  if(!workspace) preview.addEventListener('close', () => player.pause());
  player.addEventListener('error', () => { $('#preview-status').textContent = '播放暂不可用，你仍可以下载视频后查看'; });
  $('#library-create').addEventListener('click', () => { if(!workspace) library.close(); focusComposer(); });
  $$('[data-focus-prompt]').forEach(a => a.addEventListener('click', () => input.focus({preventScroll:true})));
  async function reuseDescription(plan) {
    if(uploading||posting||pendingRequest){status.textContent='请先完成当前图片准备或提交核对，再继续调整';focusComposer();return;}
    try{
      const uploaded=plan.reference_id?await api(`/api/references/${plan.reference_id}`):null;
      motionGuide.clearUndo();input.value=plan.prompt.slice(0,1200);setSelected(plan.preset);$('#aspect').value=plan.aspect;$('#duration').value=String(plan.duration);
      setReference(uploaded?null:plan.reference_preset || (!plan.status && plan.kind!=='generated-video' ? plan.preset : null),uploaded);
      updateCount();saveDraft();if(!workspace)preview.close();focusComposer();if(!activeJob())status.textContent=`已带回${hasReference()?'参考图、':''}描述和参数，修改后再点「生成我的视频」`;
    }catch(error){status.textContent=error.code?error.message:'暂时无法读取参考首帧，请稍后再试';}
  }
  $('#use-preview').addEventListener('click',()=>{if(currentPlan)reuseDescription(currentPlan);});
  $('#save-plan').addEventListener('click', () => {
    if (!currentPlan || savedCurrentId || currentJob) return;
    const id=crypto.randomUUID(); const plans=[{...currentPlan,id,savedAt:new Date().toISOString()},...readPlans()].slice(0,40);
    if (!writeJSON(libraryKey,plans)) { $('#preview-status').textContent='浏览器暂不允许保存，请保留你的描述'; return; }
    savedCurrentId=id; $('#save-plan').disabled=true; $('#save-plan span').textContent='已保存镜头方案'; $('#preview-status').textContent='已保存到当前浏览器';
  });
  $('#check-task').addEventListener('click', () => sync());
  if(workspace) {
    new IntersectionObserver(entries=>{
      const archive=entries[0].isIntersecting;
      $$('.studio-nav a[href^="#"]').forEach(link=>{
        const active=link.getAttribute('href')===(archive?'#works':'#studio');link.classList.toggle('is-active',active);
        if(active)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');
      });
      $('#workspace-section-name').textContent=archive?'我的作品':'创作空间';
    },{rootMargin:'-65px 0px -60% 0px'}).observe($('#works'));
    $$('[data-new-scene]').forEach(button=>button.addEventListener('click',()=>{
      if(uploading||posting||pendingRequest){status.textContent='请先完成当前图片上传或核对提交，再开启新镜头';focusComposer();return;}
      workspaceInitialized=true;writeJSON('prelude.workspace.selected.v1',null);
      currentJob=null;currentPlan=null;resetPlayer();setReference(null);input.value='';$('#aspect').value='16:9';$('#duration').value='';
      updateCount();updateCost();saveDraft();$('#preview-image').hidden=false;$('#preview-image').src=presets.cloud.image;
      $('.preview-visual').classList.remove('is-video');$('#workspace-welcome').hidden=false;setReference(null);$('#job-progress').hidden=true;
      $('#preview-badge').hidden=true;$('#workspace-preview-actions').hidden=true;$('#rename-work').hidden=true;$('#preview-prompt').hidden=true;
      $('#preview-title').textContent='画面预览';$('#preview-spec').textContent='等待你的下一个镜头';$('#preview-notice').textContent='生成完成后，你的视频会自动保存在下方作品集中';$('#preview-status').textContent='';
      $$('#library-items [data-job-id]').forEach(card=>card.classList.remove('is-selected'));setBusy();if(!activeJob())status.textContent='新的镜头已准备，从一句话开始';focusComposer();
    }));
    $('#rename-work').addEventListener('click',()=>{
      if(!currentJob)return;$('#work-title').value=currentJob.title||titleFrom(currentJob.prompt);$('#rename-status').textContent='';$('#rename-dialog').showModal();$('#work-title').focus();$('#work-title').select();
    });
    $('#rename-form').addEventListener('submit',async event=>{
      event.preventDefault();if(!currentJob)return;const jobId=currentJob.id,button=$('#rename-form [type="submit"]');button.disabled=true;$('#rename-status').textContent='正在保存';
      try{
        const updated=await api(`/api/generations/${jobId}/rename`,{method:'POST',headers:{'Content-Type':'application/json','X-Prelude-CSRF':csrf},body:JSON.stringify({title:$('#work-title').value.trim()})});
        jobs=jobs.map(job=>job.id===jobId?updated:job);if(currentJob?.id===jobId)renderJob(updated);renderLibrary();$('#rename-dialog').close();$('#preview-status').textContent='作品名称已保存';
      }catch(error){$('#rename-status').textContent=error.code?error.message:'暂时无法保存，请检查工作室连接';}finally{button.disabled=false;}
    });
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (uploading || posting || activeJob()) return;
    if (!input.value.trim() && !pendingRequest) { status.textContent='先写下一句话，描述你想看到的画面'; input.focus(); return; }
    if (!ready || !csrf) { await sync(true); return; }
    if ((pendingRequest?.reference_preset || referenceKey) && !supportsReference) {status.textContent='请重启本地工作室以启用参考首帧生成';return;}
    if ((pendingRequest?.reference_id || uploadedReference) && !supportsUpload) {status.textContent='请重启本地工作室以启用图片上传生成';return;}
    if (!pendingRequest) {
      pendingRequest={request_id:crypto.randomUUID(),prompt:input.value.trim(),aspect:$('#aspect').value,duration:effectiveDuration(),reference_preset:referenceKey,reference_id:uploadedReference?.id||null};
      if (!writeJSON(pendingKey,pendingRequest)) { pendingRequest=null; status.textContent='浏览器无法保存任务标识，请允许本站本地存储后再生成'; return; }
    }
    const submittedRequest = {...pendingRequest};
    saveDraft(); posting=true; setBusy(); status.textContent='正在提交你的镜头，请稍候';
    showSubmission(submittedRequest);
    try {
      const job=await api('/api/generations',{method:'POST',headers:{'Content-Type':'application/json','X-Prelude-CSRF':csrf},body:JSON.stringify(pendingRequest)});
      clearPending();if(job.deleted_at){await sync();status.textContent='原作品已在最近删除中，不会重复生成';return;} jobs=[job,...jobs.filter(j=>j.id!==job.id)]; showJob(job); if(workspace)renderLibrary();
    } catch(error) {
      if (error.code && !['server_error','csrf'].includes(error.code)) clearPending();
      status.textContent=error.code ? error.message : '连接暂时中断，正在核对原请求，请勿重新创建任务';
      disconnected=true;
      showSubmission(submittedRequest, pendingRequest ? 'UNCERTAIN' : 'REJECTED', error.code ? error.message : '');
    } finally { posting=false; setBusy(); schedulePoll(); }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
  window.addEventListener('pagehide', () => { clearTimeout(timer); player.pause(); });
  window.addEventListener('pageshow', event => { if (event.persisted) sync(); });
  sync(true);
})();
