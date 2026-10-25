(() => {
  'use strict';
  const parse=value=>{if(String(value).trim()==='')return null;const n=Number(value);return Number.isInteger(n)&&n>=2&&n<=15?n:null;};
  function recommend(prompt){
    const text=String(prompt||'').trim();
    if(!text)return {seconds:10,reason:'填写描述后，可获得时长建议'};
    const specified=text.match(/(\d{1,3})\s*秒/);
    if(specified){const seconds=Math.max(2,Math.min(15,Number(specified[1])));return {seconds,reason:Number(specified[1])===seconds?'沿用描述中提到的时长':'按当前模型的 2–15 秒范围调整'};}
    const sequence=(text.match(/首先|接着|随后|然后|最后|再(?:次|向|将|把|慢|走|转|看|飞|抬|跳)?/g)||[]).length;
    const actions=new Set(text.match(/走近|起身|转身|回头|抬头|低头|掠过|穿过|落下|升起|俯冲|降落|起飞|追逐|拥抱|转动|绽放|行驶|驶过|倒入|拿起|打开|关闭|走|跑|飞|跳|挥|伸|掀/g)||[]).size;
    if(sequence>=4||actions>=6)return {seconds:15,reason:'连续动作较多，给镜头充分展开的时间'};
    if(sequence>=3||actions>=4)return {seconds:12,reason:'包含多个动作，适合更完整的镜头节奏'};
    if(sequence>=2||actions>=3)return {seconds:10,reason:'几个动作需要自然衔接'};
    if(/慢动作|慢镜头|缓慢|慢慢|缓缓|长镜头|舒缓|徐徐|延时/.test(text))return {seconds:8,reason:'舒缓的运动，适合留出更多画面时间'};
    return {seconds:5,reason:'单个画面或动作，短镜头更集中'};
  }
  const api={parse,recommend};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else window.PreludeDuration=api;
})();
