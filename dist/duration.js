(() => {
  'use strict';
  const parse=value=>{if(String(value).trim()==='')return null;const n=Number(value);return Number.isInteger(n)&&n>=2&&n<=15?n:null;};
  function statedDuration(text){
    text=text.normalize('NFKC').toLowerCase();
    const number=token=>{
      if(/^\d/.test(token))return Number(token);
      const digits={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9};
      if(token.includes('十')){const [a,b]=token.split('十');return (a?digits[a]:1)*10+(b?digits[b]:0);}
      return digits[token];
    };
    const matches=[];
    for(const match of text.matchAll(/(\d+(?:\.\d+)?|[零一二两三四五六七八九十]{1,3})\s*(?:-\s*)?(?:秒(?!钟后)|seconds?\b|secs?\b|s\b)/g)){
      const before=text.slice(Math.max(0,match.index-32),match.index), after=text.slice(match.index+match[0].length,match.index+match[0].length+24);
      const overall=/(?:全片|总时长|总长|片长|视频时长|时长|持续|总计|duration|total(?:\s+duration)?|lasts?|lasting)\s*(?:为|是|约|共|[:：=])?\s*$/.test(before);
      const timeline=/(?:第|前|后|到|至|[–—~\-]|\b(?:at|after|before|from|until|to))\s*$/.test(before)||/^\s*(?:后|时|开始|处|内|起|[–—~\-]\s*\d)/.test(after);
      const value=number(match[1]);
      if(Number.isFinite(value)&&value>0&&!(!overall&&timeline))matches.push({value,overall});
    }
    const totals=matches.filter(item=>item.overall);
    // Ambiguous action timings do not override an explicit overall duration.
    const candidates=totals.length?totals:matches;
    return candidates.length&&new Set(candidates.map(item=>item.value)).size===1?candidates[0].value:null;
  }
  function recommend(prompt){
    const text=String(prompt||'').trim();
    if(!text)return {seconds:10,reason:'填写描述后，可获得时长建议'};
    const specified=statedDuration(text);
    if(specified!==null){const seconds=Math.max(2,Math.min(15,Math.ceil(specified)));return {seconds,reason:specified===seconds?'沿用描述中提到的时长':'按当前模型的 2–15 秒整数范围调整'};}
    const sequence=(text.match(/首先|接着|随后|然后|最后|再(?:次|向|将|把|慢|走|转|看|飞|抬|跳)?/g)||[]).length;
    const actions=new Set(text.match(/走近|起身|转身|回头|抬头|低头|掠过|穿过|落下|升起|俯冲|降落|起飞|追逐|拥抱|转动|绽放|行驶|驶过|倒入|拿起|打开|关闭|走|跑|飞|跳|挥|伸|掀/g)||[]).size;
    if(sequence>=4||actions>=6)return {seconds:15,reason:'动作较密，建议精简或拆成几段镜头'};
    if(sequence>=3||actions>=4)return {seconds:12,reason:'包含多个动作，适合更完整的镜头节奏'};
    if(sequence>=2||actions>=3)return {seconds:10,reason:'几个动作需要自然衔接'};
    if(/慢动作|慢镜头|缓慢|慢慢|缓缓|长镜头|舒缓|徐徐|延时/.test(text))return {seconds:8,reason:'舒缓的运动，适合留出更多画面时间'};
    return {seconds:5,reason:'单个画面或动作，短镜头更集中'};
  }
  function advice(prompt){
    const text=String(prompt||'');
    const dense=text.length>350&&(text.match(/随后|后续|最终|瞬间|镜头/g)||[]).length>=5;
    const high=/(?:4|8)\s*k/i.test(text);
    if(high&&dense)return '实际输出720P · 动作较密，建议精简或拆分';
    if(high)return '实际输出720P，描述中的4K/8K不会改变分辨率';
    return dense?'动作较密，建议精简或拆成几段镜头':'可填写 2–15 秒';
  }
  const api={parse,recommend,advice};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else window.PreludeDuration=api;
})();
