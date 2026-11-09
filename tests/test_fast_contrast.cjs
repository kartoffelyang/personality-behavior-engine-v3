const {test}=require('node:test');
const assert=require('node:assert/strict');
const {contrasts,highlightParts}=require('../web/static/fast-contrast.js');
const action=(id,details)=>({id,name:'回复草稿生成',details,execution_mode:'auto_generate',parameters_hint:{},preconditions:[],order:1});
const diff=(field)=>({action_id:'a1',field,factor:{quote:'真实配置引用'}});
const data=()=>({baseline:[action('a1','共同草稿')],variants:[{persona:'ESTJ',actions:[action('a1','先列问题，再安排下一步')],differences:[diff('do')]},{persona:'INFP',actions:[action('a1','共同草稿')],differences:[]}]});
test('aligns an adjusted action directly against unchanged peer without inventing attribution',()=>{
 const r=contrasts(data());assert.equal(r.length,1);assert.equal(r[0].sides[1].value,'共同草稿');assert.equal(r[0].sides[1].evidence,null);
});
test('same adjustment in both personalities is not displayed as a difference',()=>{
 const d=data();d.variants[1].actions[0].details=d.variants[0].actions[0].details;d.variants[1].differences=[diff('do')];assert.deepEqual(contrasts(d),[]);
});
test('compares parameters independent of object key insertion order',()=>{
 const d=data();d.variants.forEach(v=>{v.differences=[diff('p')];v.actions[0].details='共同草稿';});
 d.variants[0].actions[0].parameters_hint={内容:'x',语气:'y'};d.variants[1].actions[0].parameters_hint={语气:'y',内容:'x'};assert.deepEqual(contrasts(d),[]);
});
test('addition and deletion remain visible without a missing-side exception',()=>{
 const d=data();d.variants[1].actions=[];d.variants[0].differences=[diff('presence')];const r=contrasts(d);assert.equal(r.length,1);assert.equal(r[0].sides[1].value,false);
});
test('compares each changed field exactly once',()=>{
 const d=data();d.variants[1].actions[0].details='保留两种表达供选择';d.variants[1].differences=[diff('do')];assert.equal(contrasts(d).length,1);
});
test('highlights only real changed characters while preserving all input including markup',()=>{
 const strings=['先列出问题，再给建议<script>','先整理感受，再给建议<script>'];const r=highlightParts(...strings);
 r.forEach((parts,i)=>assert.equal(parts.map(x=>x.text).join(''),strings[i]));assert(r[0].some(x=>x.changed));assert(r[0].some(x=>!x.changed&&x.text.includes('再给建议')));
});
test('identical and long values remain correct',()=>{
 assert.equal(highlightParts('同一做法','同一做法')[0][0].changed,false);
 const text='很长的内容'.repeat(1000);const r=highlightParts(text+'A',text+'B');assert.equal(r[0].map(x=>x.text).join(''),text+'A');
});
