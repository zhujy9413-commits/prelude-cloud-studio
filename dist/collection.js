(() => {
  'use strict';
  window.PreludeCollection = {
    create(deps) {
      const $ = selector => document.querySelector(selector);
      const $$ = selector => [...document.querySelectorAll(selector)];
      const dialog = $('#work-detail-dialog'), video = $('#detail-video');
      const busy = new Set();
      let filter = 'all', search = '', signature = '', opened = null, previewId = null, undo = null;
      const keyOf = entry => `${entry.kind}:${entry.item.id}`;
      const all = () => [...deps.getJobs(), ...deps.getDeleted()].map(item=>({kind:'job',item})).concat(deps.getPlans().map(item=>({kind:'plan',item})));
      const find = (kind,id) => all().find(entry=>entry.kind===kind&&entry.item.id===id);
      const nameOf = item => item.title || deps.titleFrom(item.prompt);
      const active = entry => entry.kind==='job' && (!['SUCCEEDED','FAILED','REJECTED','UNAVAILABLE'].includes(entry.item.status));
      const dateOf = item => item.created_at ? new Date(item.created_at*1000) : new Date(item.savedAt);
      const dateText = item => Number.isFinite(dateOf(item).getTime()) ? dateOf(item).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '未记录';
      function notify(text, recover = null) {
        undo = recover; $('#collection-toast-text').textContent=text;
        $('#undo-trash').hidden=!recover; $('#collection-toast').hidden=false;
      }
      function actionButton(text, action, entry, symbol) {
        const button=document.createElement('button'); button.type='button'; button.className='work-action';
        button.dataset.action=action; button.dataset.kind=entry.kind; button.dataset.id=entry.item.id;
        button.setAttribute('aria-label',`${action==='favorite'&&entry.item.favorite?'取消收藏':text}《${nameOf(entry.item)}》`);
        if(symbol)button.append(deps.icon(symbol));button.append(document.createTextNode(text));
        button.disabled=busy.has(keyOf(entry)) || action==='trash'&&active(entry);
        if(action==='trash'&&active(entry))button.title='任务结束后可删除，生成不会因此取消';
        if(action==='favorite')button.setAttribute('aria-pressed',String(Boolean(entry.item.favorite)));
        button.addEventListener('click',()=>action==='detail'?open(entry):mutate(entry,action));
        return button;
      }
      function open(entry) {
        opened={kind:entry.kind,id:entry.item.id};
        // Only one foreground player should be audible at a time.
        $('#preview-video').pause(); fillDetail(entry);
        if(!dialog.open)dialog.showModal();
      }
      function fillDetail(entry) {
        const {item,kind}=entry, trashed=Boolean(item.deleted_at), complete=kind==='job'&&item.status==='SUCCEEDED'&&!trashed;
        $('#detail-title').textContent=nameOf(item);
        $('#detail-state').textContent=trashed?'已移入最近删除':kind==='plan'?'灵感方案':deps.labels[item.status]||'处理中';
        $('#detail-type').textContent=kind==='plan'?'AI 概念静帧 · 非视频':trashed?'恢复后可播放':'AI 生成视频';
        video.hidden=!complete;
        if(complete&&video.getAttribute('src')!==item.video_url){video.src=item.video_url;video.load();}
        if(!complete&&video.hasAttribute('src')){video.pause();video.removeAttribute('src');video.load();}
        $('#detail-image').hidden=complete;
        $('#detail-image').src=kind==='plan'?deps.planImage(item.preset):'assets/cloudscape.webp';
        $('#detail-placeholder').hidden=complete||kind==='plan';
        $('#detail-placeholder-text').textContent=trashed?'让这一幕，回到你的作品集':deps.labels[item.status]||'你的镜头记录';
        const parameters=[['生成方式',kind==='plan'?'参考静帧':item.reference_preset?'首帧生视频':'文生视频'],['创建时间',dateText(item)],['画面比例',item.aspect],['视频时长',`${item.duration} 秒${kind==='plan'?' · 方案参数':''}`],['清晰度',kind==='job'?item.resolution||'720P':'概念静帧'],['生成模型',kind==='job'?item.model||'wan2.6-t2v':'尚未生成视频']];
        if(item.reference_preset)parameters.push(['参考首帧',({cloud:'暮色云海',city:'雨夜东京',lunar:'月面漫游'})[item.reference_preset]||'示例图']);
        if(kind==='job')parameters.push(['标价估算',`¥${item.estimated_cost_cny} · 非实扣账单`]);
        if(trashed)parameters.push(['删除时间',new Date(item.deleted_at*1000).toLocaleString('zh-CN',{hour12:false})]);
        const list=$('#detail-parameters');list.replaceChildren();
        parameters.forEach(([label,value])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;list.append(dt,dd);});
        $('#detail-prompt').textContent=item.prompt;
        $('#detail-message').textContent=trashed?'恢复后即可继续播放、下载和复用':item.error||'';
        $('#detail-download').hidden=!complete;
        if(complete)$('#detail-download').href=item.video_url+'?download=1';else $('#detail-download').removeAttribute('href');
        $('#detail-reuse').hidden=trashed;$('#detail-progress').hidden=trashed||!active(entry);
        $('#detail-restore').hidden=!trashed;$('#detail-favorite').hidden=trashed;$('#detail-trash').hidden=trashed;
        $('#detail-favorite').setAttribute('aria-pressed',String(Boolean(item.favorite)));
        $('#detail-favorite span').textContent=item.favorite?'已收藏':'收藏作品';
        $('#detail-trash').disabled=active(entry)||busy.has(keyOf(entry));
        $('#detail-trash').title=active(entry)?'任务结束后可删除，生成不会因此取消':'';
        $('#detail-favorite').disabled=$('#detail-restore').disabled=busy.has(keyOf(entry));
      }
      function updatePreview(job) {
        previewId=job?.id||null;
        const unavailable=!job||Boolean(job.deleted_at);
        $('#preview-detail').hidden=$('#preview-favorite').hidden=unavailable;
        $('#preview-favorite').setAttribute('aria-pressed',String(Boolean(job?.favorite)));
        $('#preview-favorite').setAttribute('aria-label',job?.favorite?'取消收藏当前作品':'收藏当前作品');
        $('#preview-favorite').disabled=job?busy.has(`job:${job.id}`):false;
      }
      async function mutate(entry, action) {
        entry=find(entry.kind,entry.item.id);if(!entry||busy.has(keyOf(entry)))return;
        const {item,kind}=entry, key=keyOf(entry);busy.add(key);render();
        try {
          const values=action==='favorite'?{favorite:!item.favorite}:action==='trash'?{deleted_at:Date.now()/1000}:{deleted_at:null};
          let updated;
          if(kind==='job'){
            updated=await deps.api(`/api/generations/${item.id}/${action}`,{method:'POST',headers:{'Content-Type':'application/json','X-Prelude-CSRF':deps.csrf()},body:JSON.stringify(action==='favorite'?values:{})});
            deps.updateJob(updated);
          } else {
            updated={...item,...values};
            if(!deps.savePlans(deps.getPlans().map(plan=>plan.id===item.id?updated:plan)))throw new Error('浏览器未能保存，请检查本地存储设置');
          }
          if(action==='trash'){
            if(dialog.open&&opened?.id===item.id&&opened.kind===kind)dialog.close();
            deps.onRemoved(kind,item.id);notify('已移入最近删除，可随时恢复',{kind,id:item.id});
          } else notify(action==='restore'?'作品已恢复到作品集':updated.favorite?'已收藏这一幕':'已取消收藏');
        } catch(error) {
          notify(error.code?error.message:'操作结果暂未确认，请刷新作品集后查看');
          deps.refresh();
        } finally {
          busy.delete(key);render();
          if(!dialog.open){const target=$$('#library-items [data-action]').find(button=>button.dataset.id===item.id&&button.dataset.kind===kind&&button.dataset.action===(action==='trash'?'restore':action));(target||$('[data-work-filter][aria-pressed="true"]'))?.focus({preventScroll:true});}
        }
      }
      function render() {
        const entries=all(), live=entries.filter(entry=>!entry.item.deleted_at), deleted=entries.filter(entry=>entry.item.deleted_at);
        $('#workspace-count').textContent=String(live.length);
        $('#works-total').textContent=`${live.filter(e=>e.kind==='job').length} 个镜头${live.some(e=>e.kind==='plan')?' · '+live.filter(e=>e.kind==='plan').length+' 个方案':''}`;
        $('#favorite-count').textContent=String(live.filter(e=>e.item.favorite).length);$('#trash-count').textContent=String(deleted.length);
        $('#trash-help').hidden=filter!=='trash';
        if(previewId)updatePreview(deps.getJobs().find(item=>item.id===previewId));
        if(dialog.open&&opened){const entry=find(opened.kind,opened.id);if(entry)fillDetail(entry);else dialog.close();}
        const next=JSON.stringify([entries.map(({kind,item})=>[kind,item.id,item.title,item.prompt,item.favorite,item.deleted_at,item.status,item.video_url,item.error]),filter,search,[...busy]]);
        if(signature===next)return;signature=next;
        const shown=(filter==='trash'?deleted:live).filter(entry=>{
          if(filter==='favorite'&&!entry.item.favorite)return false;
          if(filter==='completed'&&(entry.kind!=='job'||entry.item.status!=='SUCCEEDED'))return false;
          if(filter==='active'&&!active(entry))return false;
          return `${nameOf(entry.item)} ${entry.item.prompt}`.toLocaleLowerCase().includes(search);
        }).sort((a,b)=>filter==='trash'?b.item.deleted_at-a.item.deleted_at:dateOf(b.item)-dateOf(a.item));
        thumbnails.disconnect();
        const list=$('#library-items');list.replaceChildren();$('#library-empty').hidden=shown.length>0;
        const empty=search?['没有找到这一幕','试试其他名称或描述关键词']:filter==='trash'?['最近删除，空空如也','删除的作品会保留在这里，随时可以找回']:filter==='favorite'?['把喜欢的镜头，留在这里','点击作品上的星星，收藏让你心动的一幕']:filter==='all'?['你的第一幕，还在想象中','写下一句话，让它成为第一个被看见的镜头']:['这里还没有镜头','可以切换分类，看看其他作品'];
        $('#library-empty-title').textContent=empty[0];$('#library-empty-description').textContent=empty[1];$('#library-create').hidden=Boolean(search)||filter!=='all';
        shown.forEach(entry=>{
          const {item,kind}=entry, trashed=Boolean(item.deleted_at);
          const card=document.createElement('article');card.className='library-item';card.dataset.itemId=item.id;
          if(kind==='job'){card.dataset.jobId=item.id;card.classList.toggle('is-selected',previewId===item.id);}
          const button=document.createElement('button');button.type='button';button.className='work-open';button.setAttribute('aria-label',`查看《${nameOf(item)}》详情`);button.addEventListener('click',()=>open(entry));
          const cover=document.createElement('div');cover.className='work-cover';
          if(kind==='job'&&item.status==='SUCCEEDED'&&!trashed){
            const thumb=document.createElement('video');thumb.dataset.src=item.video_url+'#t=0.1';thumb.preload='metadata';thumb.muted=true;thumb.playsInline=true;thumb.tabIndex=-1;thumb.setAttribute('aria-hidden','true');cover.append(thumb);thumbnails.observe(thumb);
            const play=document.createElement('span');play.className='work-play';play.append(deps.icon('play'));cover.append(play);
          }else if(kind==='plan'){const img=document.createElement('img');img.src=deps.planImage(item.preset);img.alt='';cover.append(img);}
          else cover.append(deps.icon(trashed?'restore':'film'));
          const hint=document.createElement('span');hint.className='card-open-hint';hint.append(document.createTextNode('查看详情'),deps.icon('diagonal'));cover.append(hint);
          const badge=document.createElement('span');badge.className='work-status';badge.textContent=trashed?'最近删除':kind==='plan'?'灵感方案':item.status==='SUCCEEDED'?'已完成':deps.labels[item.status]||'处理中';cover.append(badge);
          if(item.favorite&&!trashed){const star=document.createElement('span');star.className='favorite-mark';star.append(deps.icon('star'));star.setAttribute('aria-label','已收藏');cover.append(star);}
          const detail=document.createElement('div');detail.className='work-card-detail';
          const title=document.createElement('h3');title.textContent=nameOf(item);const text=document.createElement('p');text.textContent=item.prompt;
          const meta=document.createElement('div');meta.className='work-card-meta';meta.textContent=`${item.aspect} · ${item.duration} 秒${kind==='job'?' · 720P':' · 方案'}`;
          detail.append(title,text,meta);button.append(cover,detail);
          const actions=document.createElement('div');actions.className='work-card-actions';
          if(trashed)actions.append(actionButton('恢复作品','restore',entry,'restore'),actionButton('查看详情','detail',entry));
          else actions.append(actionButton(item.favorite?'已收藏':'收藏','favorite',entry,'star'),actionButton('查看详情','detail',entry),actionButton('删除','trash',entry,'trash'));
          card.append(button,actions);list.append(card);
        });
      }
      const thumbnails=new IntersectionObserver(entries=>entries.forEach(({target,isIntersecting})=>{if(isIntersecting&&target.dataset.src){target.src=target.dataset.src;delete target.dataset.src;thumbnails.unobserve(target);}}),{rootMargin:'160px'});
      $$('[data-work-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.workFilter;$$('[data-work-filter]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));render();}));
      $('#work-search').addEventListener('input',event=>{search=event.target.value.trim().toLocaleLowerCase();render();});
      $('#preview-detail').addEventListener('click',()=>{const entry=find('job',previewId);if(entry)open(entry);});
      $('#preview-favorite').addEventListener('click',()=>{const entry=find('job',previewId);if(entry)mutate(entry,'favorite');});
      $('#detail-favorite').addEventListener('click',()=>{const entry=opened&&find(opened.kind,opened.id);if(entry)mutate(entry,'favorite');});
      $('#detail-trash').addEventListener('click',()=>{const entry=opened&&find(opened.kind,opened.id);if(entry)mutate(entry,'trash');});
      $('#detail-restore').addEventListener('click',()=>{const entry=opened&&find(opened.kind,opened.id);if(entry)mutate(entry,'restore');});
      $('#detail-reuse').addEventListener('click',()=>{const entry=opened&&find(opened.kind,opened.id);if(entry){dialog.close();deps.reuse(entry.item);}});
      $('#detail-progress').addEventListener('click',()=>{const entry=opened&&find(opened.kind,opened.id);if(entry){dialog.close();deps.showJob(entry.item);}});
      $('#copy-work-prompt').addEventListener('click',async()=>{const entry=opened&&find(opened.kind,opened.id);if(!entry)return;try{await navigator.clipboard.writeText(entry.item.prompt);$('#detail-message').textContent='描述已复制';}catch{$('#detail-message').textContent='暂时无法复制，可以直接选中下方描述复制';}});
      $('#undo-trash').addEventListener('click',()=>{const entry=undo&&find(undo.kind,undo.id);if(entry)mutate(entry,'restore');});
      $('#dismiss-collection-toast').addEventListener('click',()=>{$('#collection-toast').hidden=true;undo=null;});
      dialog.addEventListener('close',()=>{video.pause();video.removeAttribute('src');video.load();opened=null;});
      dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
      video.addEventListener('error',()=>{$('#detail-message').textContent='播放暂不可用，可以下载视频后查看';});
      window.addEventListener('pagehide',()=>video.pause());
      return {render,updatePreview};
    }
  };
})();
