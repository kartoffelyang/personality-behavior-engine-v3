(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const form = $('fast-form'), input = $('situation'), generate = $('generate');
  let polling = null, clock = null, busy = false;
  const modeLabels = {auto_generate:'自动准备', confirm_execute:'确认后执行', choose_execute:'选择后执行', suggest_only:'仅建议'};
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  const valueText = (v, field) => field === 'mode' ? (modeLabels[v] || v) : typeof v === 'boolean' ? (v ? '采用' : '不采用') : typeof v === 'object' ? (Array.isArray(v) ? v.join('；') || '无额外条件' : Object.entries(v).map(([k,x]) => `${k}：${x}`).join('；') || '无指定参数') : String(v);
  function remember(value) { try { sessionStorage.setItem('fast-plan', JSON.stringify(value)); } catch (_) {} }
  function error(message) { $('form-error').textContent = message; $('form-error').hidden = !message; }
  function stop() { clearTimeout(polling); clearInterval(clock); busy = false; generate.disabled = false; input.disabled = false; document.querySelectorAll('[data-example]').forEach(n => n.disabled = false); $('output').setAttribute('aria-busy','false'); }
  function loading(started) {
    busy = true; generate.disabled = true; input.disabled = true; document.querySelectorAll('[data-example]').forEach(n => n.disabled = true);
    $('empty').hidden = true; $('result').hidden = true; $('loading').hidden = false; $('output').setAttribute('aria-busy','true');
    const tick = () => { const s = Math.max(0, Math.floor((Date.now()-started)/1000)); $('elapsed').textContent = `${s} 秒`; if(s > 45) $('progress').textContent = '模型仍在生成，请继续等待。'; };
    tick(); clearInterval(clock); clock = setInterval(tick, 1000);
  }
  function fullPlan(variant) {
    const full=el('details'); full.append(el('summary',`${variant.persona} 完整行为 · ${variant.actions.length} 项`)); const list=el('ol');
    for(const a of variant.actions) { const li=el('li',a.details); li.append(el('p',`${a.name} · ${a.mode_label}`)); if(a.preconditions.length) li.append(el('p',`前提：${a.preconditions.join('；')}`)); if(Object.keys(a.parameters_hint).length) li.append(el('p',valueText(a.parameters_hint,'p'))); list.append(li); }
    full.append(list); return full;
  }
  function sideText(side,field) {
    if(field==='presence') return side.action ? `采用：${side.action.details}` : '不采用这项行为';
    if(field==='order') return `第 ${side.value} 步`;
    return valueText(side.value,field);
  }
  function render(data) {
    const root=$('result');root.replaceChildren();$('loading').hidden=true;root.hidden=false;
    const jid=new URLSearchParams(location.search).get('job');
    
    if(data.status==='no_tool_needed')root.append(el('p','本轮没有生成工具动作。','status-note'));
    if(!jid)return;
    const frame=el('iframe');frame.title='行为、人格依据与模拟执行';frame.src=`/soa-sim?job=${encodeURIComponent(jid)}&embed=1`;frame.style.cssText='width:100%;min-height:850px;border:0;display:block';root.append(frame);
  }
  window.addEventListener('message',event=>{
    const frame=$('result').querySelector('iframe');
    if(event.origin===location.origin&&frame&&event.source===frame.contentWindow&&event.data?.type==='behavior-continue'&&/^[a-f0-9]{32}$/.test(event.data.job)){location.assign('/fast?job='+event.data.job);return;}
    if(event.origin===location.origin&&frame&&event.source===frame.contentWindow&&event.data?.type==='behavior-height'&&Number.isFinite(event.data.height))frame.style.height=Math.min(30000,Math.max(500,event.data.height))+'px';
  });
  async function poll(id) {
    try {
      const response=await fetch(`/api/fast/${encodeURIComponent(id)}`); const job=await response.json();
      if(!response.ok) throw new Error(job.error || '无法读取任务，请重新生成。');
      if(job.status === 'completed') { if(job.input) input.value=job.input; history.replaceState(null,'',`/fast?job=${encodeURIComponent(job.id)}`); remember({id:job.id,text:input.value,started:Date.now()}); stop(); render(job.result); return; }
      if(job.status === 'failed') { stop(); $('loading').hidden=true; $('empty').hidden=false; error(job.error); remember(null); return; }
      $('progress').textContent = job.status === 'queued' ? '任务已提交，等待开始。' : '正在生成共同任务行为与两种人格的调整。';
      polling=setTimeout(()=>poll(id),1000);
    } catch(e) { stop(); $('loading').hidden=true; $('empty').hidden=false; error(e.message); }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if(busy) return; const text=input.value.trim(); if(!text) { input.focus(); return; } error('');
    if($('scenario-note')) $('scenario-note').textContent='正在生成新的结果，不复用已存试跑。';
    const started=Date.now(); loading(started); $('progress').textContent='正在提交…';
    try { const response=await fetch('/api/fast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})}); const data=await response.json(); if(!response.ok) throw new Error(data.error || '提交失败，请重试。'); remember({id:data.id,text,started}); history.replaceState(null,'',`/fast?job=${encodeURIComponent(data.id)}`); poll(data.id); }
    catch(e) { stop(); $('loading').hidden=true; $('empty').hidden=false; error(e.message); }
  });
  document.querySelectorAll('[data-example]').forEach(button=>button.addEventListener('click',()=>{
    if(busy) return;
    input.value=button.dataset.example; error('');
    const jid=button.dataset.resultId;
    document.querySelectorAll('[data-example]').forEach(n=>n.setAttribute('aria-pressed',String(n===button)));
    if(jid) {
      $('scenario-note').textContent='正在查看该场景的已存试跑结果；点击生成可重新运行。';
      history.replaceState(null,'',`/fast?job=${encodeURIComponent(jid)}`);
      loading(Date.now()); poll(jid);
    } else {
      remember(null); history.replaceState(null,'','/fast');
      $('result').hidden=true; $('empty').hidden=false;
      $('scenario-note').textContent=button.dataset.trialError || '情境已填入，点击生成行为与差异。';
      input.focus();
    }
  }));
  try { const saved=JSON.parse(sessionStorage.getItem('fast-plan')); const linked=new URLSearchParams(location.search).get('job'); if(linked && /^[a-f0-9]{32}$/.test(linked)) { loading(Date.now()); poll(linked); } else if(saved?.id && saved.text) { input.value=saved.text; loading(saved.started || Date.now()); poll(saved.id); } } catch(_) {}
})();
