const {test}=require('node:test');const assert=require('node:assert/strict');const {analyze}=require('../web/static/behavior-evidence.js');
const a=(id,p={},details='搜索')=>({id,capability:'entertainment',name:'内容搜索',parameters_hint:p,details});
const plan=(l,r)=>({variants:[{actions:l},{actions:r}]});
test('wording alone is not a service difference',()=>assert.deepEqual(analyze(plan([a('a')],[a('a',{},'搜索不同类型')])),{callDifferences:0,descriptionDifferences:1,policyDifferences:0}));
test('parameter changes and order are visible',()=>{assert.equal(analyze(plan([a('a',{关键词:'轻音乐'})],[a('a',{关键词:'播客'})])).callDifferences,1);assert.equal(analyze(plan([a('a'),a('b')],[a('b'),a('a')])).callDifferences,2);});
test('object key order does not manufacture a difference',()=>assert.equal(analyze(plan([a('a',{甲:1,乙:2})],[a('a',{乙:2,甲:1})])).callDifferences,0));
test('added actions are concrete differences',()=>assert.equal(analyze(plan([a('a')],[a('a'),a('b')])).callDifferences,1));

test('authorization and conditions differ without inventing a service change',()=>{const left=a('a'),right={...a('a'),execution_mode:'choose_execute',preconditions:['选择后']};const r=analyze(plan([left],[right]));assert.equal(r.callDifferences,0);assert.equal(r.policyDifferences,1);});
