(function(root){
'use strict';
const canonical=x=>Array.isArray(x)?'['+x.map(canonical).join(',')+']':x&&typeof x==='object'?'{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}':JSON.stringify(x);
function indexed(actions){const seen=new Map();return actions.map(a=>{const key=a.domain+' / '+a.name,n=seen.get(key)||0;seen.set(key,n+1);return {key:key+' #'+n,action:a};});}
function comparison(variants){
 const a=variants.ESTJ,b=variants.INFP;if(a?.status!=='completed'||b?.status!=='completed')return null;
 const left=indexed(a.actions),right=indexed(b.actions),rows=[];
 const matching=canonical(a.actions.map(x=>[x.domain,x.name,x.parameters]))===canonical(b.actions.map(x=>[x.domain,x.name,x.parameters]));
 for(const l of left)if(!right.some(r=>r.key===l.key))rows.push([l.action.name,l.action,null]);
 for(const r of right)if(!left.some(l=>l.key===r.key))rows.push([r.action.name,null,r.action]);
 for(const l of left){const r=right.find(x=>x.key===l.key);if(r&&!/搜索|详情|查询|获取/.test(l.action.name)&&canonical(l.action.parameters)!==canonical(r.action.parameters))rows.push([l.action.name,l.action,r.action]);}
 if(!rows.length)for(const l of left){const r=right.find(x=>x.key===l.key);if(r&&l.action.text!==r.action.text)rows.push([l.action.name,l.action,r.action]);}
 const choiceIds=a=>{const p=a?.parameters||{},value=p.POI||p.内容||p.候选项||'';return [...new Set(String(value).match(/[RMP]\d+/g)||[])];};
 const priority=row=>{const a=choiceIds(row[1]),b=choiceIds(row[2]);if(a.length===1&&b.length===1&&a[0]!==b[0])return 0;return /播放|通知|按摩|温度/.test(row[0])?1:/搜索|详情|比较|筛选|查询|获取|判断/.test(row[0])?3:2;};
 rows.sort((a,b)=>priority(a)-priority(b));return {matching,rows:rows.slice(0,1)};
}
function actionDifferences(variants){
 const a=variants.ESTJ,b=variants.INFP;if(a?.status!=='completed'||b?.status!=='completed')return [];
 const left=indexed(a.actions),right=indexed(b.actions),keys=[...new Set([...left,...right].map(x=>x.key))];
 return keys.map(key=>{const l=left.find(x=>x.key===key)?.action||null,r=right.find(x=>x.key===key)?.action||null;
   const kind=!l||!r?'presence':canonical(l.parameters)!==canonical(r.parameters)?'parameters':l.text!==r.text?'description':'same';
   return {key,name:(l||r).name,left:l,right:r,kind};});
}
function visibleActions(actions){const groups=new Map();for(const a of actions)if(!/搜索|详情|比较|筛选|查询|获取|判断/.test(a.name))groups.set(a.domain,a);return (groups.size?[...groups.values()].sort((a,b)=>a.order-b.order):actions.slice(-2)).slice(0,3).map(a=>{const selected=actions.find(x=>x.domain===a.domain&&/内容选择|候选方案选择/.test(x.name));return selected&&selected!==a&&/已选|选定/.test(a.text)?{...a,text:selected.text+'；'+a.text}:a;});}
const api={comparison,visibleActions,actionDifferences};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DemoContrast=api;
})(typeof window==='undefined'?{}:window);
