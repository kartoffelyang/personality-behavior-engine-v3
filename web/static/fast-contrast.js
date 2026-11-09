/* Pure comparison of saved plans: no model calls or invented personality labels. */
(function (root) {
  'use strict';
  const labels = {do:'处理方式',mode:'执行方式',p:'具体参数',when:'执行条件',presence:'是否采用',order:'行动顺序'};
  const fields = {do:'details',mode:'execution_mode',p:'parameters_hint',when:'preconditions',order:'order'};
  const canonical = value => JSON.stringify(value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.keys(value).sort().map(k=>[k,JSON.parse(canonical(value[k]))])) : Array.isArray(value) ? value.map(v=>JSON.parse(canonical(v))) : value);
  function contrasts(data) {
    const variants = ['ESTJ','INFP'].map(pid=>data.variants.find(v=>v.persona===pid) || {persona:pid,actions:[],differences:[]});
    const maps = variants.map(v=>new Map(v.actions.map(a=>[a.id,a])));
    const ids = [...new Set([...(data.baseline || []).map(a=>a.id), ...variants.flatMap(v=>v.actions.map(a=>a.id))])];
    const rows=[];
    for(const id of ids) {
      const actions = maps.map(m=>m.get(id));
      const evidence = variants.map(v=>v.differences.filter(d=>d.action_id===id));
      const candidates = [...new Set(evidence.flatMap(ds=>ds.map(d=>d.field)))];
      for(const field of candidates) {
        if(!labels[field]) continue;
        if(field!=='presence' && (!actions[0] || !actions[1])) continue;
        const values = actions.map(a=>field==='presence' ? Boolean(a) : a?.[fields[field]]);
        if(canonical(values[0])===canonical(values[1])) continue;
        const action = actions.find(Boolean) || (data.baseline || []).find(a=>a.id===id);
        rows.push({action_id:id,name:action?.name || id,field,label:labels[field],sides:variants.map((v,i)=>({persona:v.persona,action:actions[i] || null,value:values[i],evidence:evidence[i].find(d=>d.field===field) || null}))});
      }
    }
    const priority={presence:0,mode:1,p:2,when:3,order:4,do:5};
    return rows.sort((a,b)=>priority[a.field]-priority[b.field]);
  }
  // Mark only characters that actually differ. Cap work for unusually long values.
  function highlightParts(left,right) {
    const a=Array.from(left), b=Array.from(right);
    if(left===right) return [[{text:left,changed:false}],[{text:right,changed:false}]];
    let commonA=new Set(),commonB=new Set();
    if(a.length*b.length<=160000) {
      const dp=Array.from({length:a.length+1},()=>new Uint16Array(b.length+1));
      for(let i=a.length-1;i>=0;i--) for(let j=b.length-1;j>=0;j--) dp[i][j]=a[i]===b[j]?dp[i+1][j+1]+1:Math.max(dp[i+1][j],dp[i][j+1]);
      let i=0,j=0;
      while(i<a.length&&j<b.length) { if(a[i]===b[j]) {commonA.add(i++);commonB.add(j++);} else if(dp[i+1][j]>=dp[i][j+1]) i++;else j++; }
    } else {
      let i=0;while(i<Math.min(a.length,b.length)&&a[i]===b[i]) {commonA.add(i);commonB.add(i++);}
      let x=a.length-1,y=b.length-1;while(x>=i&&y>=i&&a[x]===b[y]) {commonA.add(x--);commonB.add(y--);}
    }
    const group=(text,same)=>text.reduce((out,ch,i)=>{const changed=!same.has(i),last=out[out.length-1];if(last&&last.changed===changed)last.text+=ch;else out.push({text:ch,changed});return out;},[]);
    return [group(a,commonA),group(b,commonB)];
  }
  const api={contrasts,highlightParts};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.FastContrast=api;
})(typeof window==='undefined'?{}:window);
