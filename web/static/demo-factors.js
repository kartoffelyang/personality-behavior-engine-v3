(function(root){
'use strict';
const canonical=x=>Array.isArray(x)?'['+x.map(canonical).join(',')+']':x&&typeof x==='object'?'{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}':JSON.stringify(x);
function parameterDifferences(left,right){const a=left?.parameters||{},b=right?.parameters||{};return [...new Set([...Object.keys(a),...Object.keys(b)])].filter(k=>canonical(a[k])!==canonical(b[k]));}
const aliases={'主动性':/主动|下一步建议/,'控制权保留度':/自主|控制权|修改|接管|停止/,'解释透明度':/解释|说明依据|呈现依据/,'记忆调用强度':/历史|熟悉|复用/,'情感投入':/情绪|感受|接纳|疲惫/,'排序结构化':/优先级|先后|排序|阶段/,'服务延伸':/额外|追加|延伸/,'时机敏感':/时机|时点|条件满足/};
function factorsFor(action,metadata){
 if(!action)return [];
 const text=action.why||'';
 if(/KEV独立判断/.test(text))return []; // Independent factor predictions are not action explanations.
 if(!text||/人格未改变|^无人格/.test(text))return [];
 const configured=[];
 for(const [name,phrase,source] of [['场景策略',metadata?.scene_strategy?.scenario_strategy,'本轮Step 5场景策略'],['行动策略',metadata?.scene_strategy?.action_strategy,'本轮Step 4–5功能策略'],['主导功能',metadata?.profile?.dominant_name,'本轮Step 3人格画像']]){if(phrase&&text.includes(phrase)){configured.push({dimension:name,relation:'explicit',label:phrase+' · 明确提及',description:metadata?.scene_strategy?.cross_scenario_prototype||phrase,function_text_source:source});break;}}
 const dimensions=(metadata?.dimensions||[]).flatMap(d=>{
   if(text.includes(d.dimension))return [{...d,relation:'explicit',label:d.dimension+' · 明确提及'}];
   if(aliases[d.dimension]?.test(text))return [{...d,relation:'text_link',label:d.dimension+' · 文本关联'}];
   return [];
 });
 return [...configured,...dimensions];
}
function summary(action,keys,kind){
 if(!action)return '未采用';
 if(kind==='presence')return '采用 · '+action.text;
 if(kind==='parameters')return keys.map(k=>k+'：'+(Object.hasOwn(action.parameters||{},k)?canonical(action.parameters[k]):'未设置')).join('；');
 return action.text;
}
const api={parameterDifferences,factorsFor,summary};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DemoFactors=api;
})(typeof window==='undefined'?{}:window);
