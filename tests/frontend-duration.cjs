const assert=require('node:assert/strict');
const duration=require('../dist/duration.js');
for(const [prompt,seconds] of [
  ['镜头在第2秒开始推进，全片持续15秒',15],
  ['全片15秒，第2秒开始推进',15],
  ['15-second continuous shot, a woman turns slowly',15],
  ['十五秒长镜头，人物转身并抬手',15],
  ['总时长为十秒，第两秒回头',10],
  ['At 2 seconds she turns. Total duration: 15 seconds.',15],
  ['总长 8.5 秒，缓慢推进',9],
  ['总时长 30秒',15],['全片1秒',2],['持续１２秒',12],
  ['第2秒开始推进，缓慢穿过云层',8],
  ['0-2秒静止，2-4秒回头',5],
  ['After 2 seconds, a woman turns',5],
  ['200 milliseconds, a quiet sky',5],
  ['全片15秒，人物停顿2秒后起身',15],
]) assert.equal(duration.recommend(prompt).seconds,seconds,prompt);
assert.equal(duration.parse('7'),7);
for(const value of ['', '7.5','16','1','NaN'])assert.equal(duration.parse(value),null);
// The composer uses a valid manual duration before consulting recommendations.
const effective=(manual,prompt)=>duration.parse(manual)??duration.recommend(prompt).seconds;
assert.equal(effective('7','全片15秒'),7);
assert.equal(effective('','全片15秒'),15);
console.log('PASS: overall duration, timeline exclusion, Chinese/English numerals, bounds and manual precedence');
