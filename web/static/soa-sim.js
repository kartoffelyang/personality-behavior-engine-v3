(() => {
  'use strict';
  const $=id=>document.getElementById(id), el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const query=new URLSearchParams(location.search), job=query.get('job');
  if(query.get('embed')==='1'){document.body.classList.add('embedded');new ResizeObserver(()=>parent.postMessage({type:'behavior-height',height:document.documentElement.scrollHeight},location.origin)).observe(document.body);}
  let persona=query.get('persona')==='INFP'?'INFP':'ESTJ', session=null,plan=null,sessions={},busy=false;
  const storeKey=()=>`soa-sim:${job}:${persona}`;
  const scalar=x=>typeof x==='object'?JSON.stringify(x):String(x);
  function message(text){$('sim-error').textContent=text;$('sim-error').hidden=!text;}
  function lock(value){busy=value;document.querySelectorAll('button,input,select').forEach(n=>n.disabled=value);}
  async function api(url,body){const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw new Error(data.error||'模拟请求未完成');return data;}
  async function load(reset=false){
    if(busy)return;
    if(!job||!/^[a-f0-9]{32}$/.test(job)){message('请先在行为结果页选择一个已生成的计划。');$('step-title').textContent='没有可加载的计划';return;}
    message('');lock(true);
    try{
      if(!plan){const record=await api('/api/fast/'+job);if(!record.result)throw new Error('行为方案尚未生成完成');plan=record.result;}
      await Promise.all(['ESTJ','INFP'].map(async pid=>{
        const key=`soa-sim:${job}:${pid}`;let sid,saved=null;
        try{sid=reset?null:sessionStorage.getItem(key);}catch(_){}
        if(sid){try{saved=await api('/api/soa-sim/sessions/'+sid);}catch(_){}}
        if(!saved||saved.fixture_version!==2)saved=await api('/api/soa-sim/sessions',{job_id:job,persona:pid});
        saved=await api(`/api/soa-sim/sessions/${saved.id}/prepare`,{revision:saved.revision});
        sessions[pid]=saved;try{sessionStorage.setItem(key,saved.id);}catch(_){}
      }));
      session=sessions[persona];
      render();
    }catch(e){message(e.message);}finally{lock(false);}
  }
  function field(spec){
    const wrap=el('div'),label=el('label',spec.name);const id='arg-'+spec.name;label.htmlFor=id;wrap.append(label);
    let input;
    if(spec.choices.length||spec.type==='boolean'){
      input=el('select');const none=el('option','请选择');none.value='';input.append(none);
      const choices=spec.type==='boolean'?[{id:'true',name:'开启 / 是'},{id:'false',name:'关闭 / 否'}]:spec.choices;
      for(const choice of choices){const option=el('option',choice.name||choice.label||choice.id);option.value=choice.id;input.append(option);}
    }else{input=el('input');input.type=spec.type==='number'?'number':'text';if(spec.type==='number'){input.min=spec.min;input.max=spec.max;input.step='any';}input.placeholder=spec.name==='任务时间'?'例如 10:00':'请补充模拟调用参数';}
    input.name=spec.name;input.id=id;input.required=true;
    if(spec.value!==undefined&&spec.value!==null)input.value=scalar(spec.value);
    wrap.append(input);return wrap;
  }
  function render(){
    $('back-link').href='/fast?job='+job;$('input-description').textContent=session.input||'';
    document.querySelectorAll('[data-persona]').forEach(n=>n.setAttribute('aria-pressed',String(n.dataset.persona===persona)));
    const next=session.pending;$('step-form').hidden=!next;$('dispatch-controls').hidden=!next;
    $('step-title').textContent=next?`${session.cursor+1}. ${next.action.name}`:'该计划步骤已结束';
    $('service-method').textContent=next?`${next.service} / ${next.method}`:'可检查日志，或从相同初始状态重新运行。';
    $('preconditions').textContent=next?.preconditions.length?`计划前提：${next.preconditions.join('；')}`:'';
    $('arguments').replaceChildren();
    if(next){for(const spec of next.required_fields)$('arguments').append(field(spec));$('execute').textContent=next.needs_confirmation?'确认并执行这一步模拟调用 →':'执行这一步模拟查询 →';}
    $('step-count').textContent=`计划进度 ${session.cursor} / ${session.actions.length} · ${persona} 独立模拟状态`;
    sessions[persona]=session;renderDirectResults();renderBehavior();renderState();
  }
  function choose(pid,item,kind){
    if(busy)return;persona=pid;session=sessions[pid];render();
    if(kind==='media')run({manual_content:item.id,confirmed:true});
    else if(session.pending?.action.name==='候选方案选择')run({params:{候选项:item.id},confirmed:true});
  }
  function renderContextGap(root,diagnosis){
    root.append(el('h2',diagnosis.title),el('p',`已理解的目标：${diagnosis.goal}`,'understood-goal'),el('p',diagnosis.detail));
    root.append(el('p',`本轮实际调用 ${diagnosis.calls} 次 · 未执行车控 · 尚未进入人格行为对比`,'result-conclusion'));
    const steps=el('ol',undefined,'processing-status');for(const text of ['意图理解：已完成','执行上下文：待补充','行为规划：尚无可执行动作','服务执行：未开始'])steps.append(el('li',text));root.append(steps);
    if(diagnosis.questions.length){
      const form=el('form');form.className='clarification-form';
      diagnosis.questions.forEach((question,i)=>{const label=el('label',question);label.htmlFor='clarification-'+i;const input=el('input');input.id=label.htmlFor;input.name=String(i);input.required=true;input.maxLength=500;input.placeholder=question.includes('时间')?'例如：今天 10:00 前到达':'请填写具体信息';form.append(label,input);});
      const note=el('p','未连接真实导航，无法自动读取当前目的地或预计到达时间；填写的信息会用于新一轮规划。','sim-small'),button=el('button','补充信息并继续');button.type='submit';const error=el('p',undefined,'error');error.setAttribute('role','alert');form.append(note,button,error);
      form.addEventListener('submit',async event=>{event.preventDefault();if(busy)return;lock(true);error.textContent='';button.textContent='正在提交补充信息…';
        try{const answers=[...form.querySelectorAll('input')].map((input,i)=>`${diagnosis.questions[i]}：${input.value.trim()}`);
          const text=`${session.input}\n补充信息：\n${answers.join('\n')}`;
          if(text.length>3000)throw new Error('补充后的情境超过3000字，请精简。');
          const next=await api('/api/fast',{text});
          const url='/fast?job='+encodeURIComponent(next.id);
          if(query.get('embed')==='1')parent.postMessage({type:'behavior-continue',job:next.id},location.origin);else location.assign(url);
        }catch(e){error.textContent=e.message;button.textContent='补充信息并继续';lock(false);}
      });root.append(form);
    }
    const logic=el('details');logic.append(el('summary','这条任务为何停住？当前系统实际如何工作？'),el('p','当前流程：一句用户输入 → 一次模型生成共同方案与人格调整 → 参数、依赖和权限校验 → 固定本地模拟服务。规划时没有车端导航状态，也没有服务返回后的模型重规划。'),el('p','这条输入已识别为时间压力场景，但目的地和到达期限都未知；本轮模型返回 needs_clarification，动作列表为空。没有调用不等于接口超时，也不是在等车控确认。'),el('p','两种人格共享任务、安全、依赖和授权约束；Te / Fi 在此是模型提示策略，并非两套强制行为规则。没有动作时，不能据此判断人格是否有差异。'));root.append(logic);
  }
  function renderDirectResults(){
    const root=$('direct-results');root.replaceChildren();
    const diagnosis=ExecutionStatus.diagnose(plan,session),noActions=!session.actions.length;
    document.querySelector('.sim-toolbar').hidden=noActions;document.querySelector('.technical-details').hidden=noActions;document.querySelector('.theory').hidden=noActions;document.querySelector('.state-snapshot').hidden=noActions;$('step-count').hidden=noActions;$('step-title').hidden=noActions;
    if(noActions){renderContextGap(root,diagnosis);return;}
    const lists=['ESTJ','INFP'].map(pid=>{const s=sessions[pid];return s?.context.service_candidates?.length?{kind:'service',items:s.context.service_candidates}:s?.context.media_candidates?.length?{kind:'media',items:s.context.media_candidates}:{kind:'none',items:[]};});
    const bothReady=lists.every(x=>x.items.length),same=bothReady&&JSON.stringify(lists[0])===JSON.stringify(lists[1]);
    root.append(el('h2',bothReady?(same?'两种人格本轮给出相同候选':'两种人格的具体结果对照'):'本轮执行结果'));
    root.append(el('p',bothReady?'以下为本地模拟服务实际返回的具体内容；商户、距离、价格和等待时间均为演示数据。':'未查询到可展示的具体候选。可展开下方调用查看缺少的信息或执行条件。','sim-small'));
    const grid=el('div',undefined,'recommendation-pair');
    ['ESTJ','INFP'].forEach((pid,i)=>{
      const s=sessions[pid],list=lists[i],side=el('section',undefined,'recommendation-side');side.append(el('h3',pid));
      if(same)side.append(el('p','候选相同 · 未落实不同排序','result-badge'));
      const selected=s.state.ecosystem.selected;
      if(selected)side.append(el('p',`已选择：${selected.name}。仅记录模拟选择，未导航或预约。`,'selected-result'));
      if(!list.items.length){const latest=s.events.at(-1);const status=ExecutionStatus.diagnose(plan,s);side.append(el('p',latest?eventFeedback(latest):status.title),el('p',status.detail,'sim-small'));if(s.pending)side.append(el('p','展开下方“策略依据与逐步调用”可填写参数并确认。','sim-small'));}
      for(const item of list.items){const card=el('article',undefined,'recommendation-item');card.append(el('h4',item.name));
        if(list.kind==='service'){
          if(item.dish)card.append(el('p',item.dish,'concrete-content'));
          card.append(el('p',`${item.distance_km} km · 人均 ¥${item.price} · 评分 ${item.rating}`,'result-facts'));
          if(item.wait_minutes!==undefined)card.append(el('p',`预计等待 ${item.wait_minutes} 分钟 · 绕行 ${item.detour_minutes} 分钟 · 用餐 ${item.meal_minutes} 分钟`,'result-facts'));
          card.append(el('p',item.description||'固定演示候选','result-reason'));
          if(s.pending?.action.name==='候选方案选择'){const button=el('button','选择这家（模拟）');button.type='button';button.addEventListener('click',()=>choose(pid,item,'service'));card.append(button);}
        }else{
          card.append(el('p',`${item.type} · ${item.duration_seconds} 秒 · ${item.author}`,'result-facts'));
          const button=el('button','选择并模拟播放');button.type='button';button.addEventListener('click',()=>choose(pid,item,'media'));card.append(button);
        }
        side.append(card);
      }
      if(list.kind==='media'&&s.state.entertainment.selected)side.append(el('p',`模拟播放器：${s.state.entertainment.playing?'播放中':'未播放'} · ${s.state.entertainment.selected.name}`,'selected-result'));
      const failed=s.events.find(e=>e.response.code!=='OK');if(failed)side.append(el('p',`未完成：${failed.response.message}`,'error'));
      grid.append(side);
    });root.append(grid);
    if(same)root.append(el('p','为什么相同：两种人格提交的查询参数一致；原计划没有可执行的不同筛选或排序参数，所以不能把措辞差异当作不同推荐。','result-conclusion'));
  }
  function renderState(){
    const s=session.state,root=$('vehicle-state');root.replaceChildren();const grid=el('div',undefined,'state-grid');
    const changed=session.events.at(-1)?.state_changes||{};
    const groups=[
      ['entertainment','模拟播放器',s.entertainment.playing?'播放中':'未播放',[s.entertainment.selected?.name||'未选择内容',`音量 ${s.entertainment.volume} · ${s.entertainment.source}`]],
      ['climate','空调',`${s.climate.temperature} °C`,[`空调${s.climate.power?'开启':'关闭'} · 风量 ${s.climate.fan}`,`送风 ${s.climate.air_mode}`]],
      ['ambient_light','氛围灯',s.ambient_light.power?s.ambient_light.color:'关闭',[`亮度 ${s.ambient_light.brightness} · ${s.ambient_light.effect}`]],
      ['seat','座椅',s.seat.massage?'按摩开启':'按摩关闭',[`加热 ${s.seat.heat} · 通风 ${s.seat.ventilation}`,`靠背 ${s.seat.backrest}°`]],
      ['navigation','导航',s.navigation.running?'导航中':'未启动',[s.navigation.destination?.name||'未设置目的地',s.navigation.route?.label||'未选路线']],
      ['notification_mode','通知与服务',s.notification_mode.muted?'通知静音':'通知正常',[`草稿 ${s.notification_mode.draft?'已准备':'未准备'} · 模拟发送 ${s.notification_mode.outbox.length}`,`演示订单 ${s.ecosystem.orders.length} · 演示支付 ${s.ecosystem.payments.length}`]]
    ];
    for(const [id,label,value,lines] of groups){const section=el('section',undefined,'state-section'+(changed[id]?' changed':''));section.append(el('h3',label),el('strong',value));for(const line of lines)section.append(el('p',line));grid.append(section);}
    root.append(grid,el('p','模拟播放只改变页面状态，不播放实际音频。','sim-small'));
  }
  function traceFor(event){
    const trace=el('details');trace.append(el('summary',`${event.request.service} / ${event.request.method} · ${event.response.code} · 本地 ${event.elapsed_ms} ms`));const grid=el('div',undefined,'trace-grid');
    for(const [title,data] of [['请求',event.request],['响应',event.response],['状态差异',event.state_changes]]){const part=el('div');part.append(el('h4',title),el('pre',JSON.stringify(data,null,2)));grid.append(part);}trace.append(grid);return trace;
  }
  function eventFeedback(event){
    if(event.response.code!=='OK')return `执行失败：${event.response.message}；模拟状态未改变。`;
    const changes=event.state_changes;
    if(changes.entertainment){const s=changes.entertainment.after;return `模拟播放器：${s.playing?'播放中':'未播放'}，${s.selected?.name||'未选择内容'}，音量 ${s.volume}`;}
    if(changes.climate){const s=changes.climate.after;return `模拟空调：${s.power?'开启':'关闭'}，${s.temperature}℃，风量 ${s.fan}`;}
    const domains=Object.keys(changes);if(domains.length)return `模拟状态已更新：${domains.join('、')}（具体值见调用明细）`;
    const data=event.response.data||{},items=data.candidates||data.details||data.routes||data.items;
    return Array.isArray(items)?`返回 ${items.length} 项演示数据；车况未改变。`:'服务返回成功；车况未改变。';
  }
  function renderBehavior(){
    const report=BehaviorEvidence.analyze(plan),verdict=$('behavior-verdict');verdict.replaceChildren();verdict.hidden=!session.actions.length;
    verdict.append(el('p',`差异结论：${report.callDifferences} 项调用差异 · ${report.policyDifferences} 项执行条件差异 · ${report.descriptionDifferences} 项描述差异`,'difference-summary')); 
    if(report.policyDifferences)verdict.append(el('p',`另有 ${report.policyDifferences} 项执行方式或条件差异；同一服务也可能在不同条件下调用。`));
    verdict.append(el('p',report.callDifferences?'下面逐步展示差异、配置依据与模拟执行结果；执行条件需单独确认。':report.policyDifferences?'差异在执行方式或条件，见下方逐步对照；服务名、参数和顺序相同。':report.descriptionDifferences?'目前只有策略描述不同，尚未落实为不同调用。相同初始状态和相同输入参数会得到相同模拟结果。':'本轮未生成可观察的人格行为差异。'));
    $('config-version').textContent=`本轮方案配置版本：${plan.config_version}。每项引用来自本轮保存记录；本页不补造人格归因。`;
    const root=$('behavior-timeline'),controls=$('dispatch-controls');controls.hidden=true;root.before(controls);root.replaceChildren();
    const variants=plan.variants,ids=[...new Set(variants.flatMap(v=>v.actions.map(a=>a.id)))];
    for(const id of ids){
      const row=el('article',undefined,'behavior-step'),sides=variants.map(v=>({variant:v,action:v.actions.find(a=>a.id===id)}));
      const current=session.actions.find(a=>a.id===id),events=session.events.filter(e=>e.step_id===id&&e.request.source==='generated_plan'),index=session.actions.findIndex(a=>a.id===id);
      const heading=el('header');heading.append(el('h3',`${current?index+1+'. ':''}${sides.find(s=>s.action)?.action.name||id}`),el('span',!current?'本人格未采用':index<session.cursor?'已执行':index===session.cursor?'待执行':'等待前序','step-status'));row.append(heading);
      const pair=el('div',undefined,'behavior-pair');
      for(const {variant,action} of sides){const cell=el('div',undefined,'behavior-side'+(variant.persona===persona?' selected':''));cell.append(el('h4',variant.persona));
        if(!action){cell.append(el('p','本方案不采用该动作'));pair.append(cell);continue;}
        cell.append(el('p',action.details),el('p',`${action.capability} / ${action.name} · ${action.mode_label}`,'sim-small'),el('p',`参数：${JSON.stringify(action.parameters_hint)}`,'service-method'));
        if(action.preconditions?.length)cell.append(el('p','执行条件：'+action.preconditions.join('；'),'sim-small'));
        const evidence=variant.differences.filter(d=>d.action_id===id);
        if(evidence.length)for(const d of evidence){const why=el('div',undefined,'behavior-why');why.append(el('p',`${d.factor.function} ${d.factor.function_name}：${d.factor.quote}`),el('p',d.link_explanation||'本轮仅记录配置关联，未给出具体解释。'));if(d.field==='do')why.append(el('p','落地情况：仅描述变化，没有由此自动生成额外服务参数。','sim-small'));const source=el('details');source.append(el('summary','查看依据'),el('p',d.factor.source));why.append(source);cell.append(why);}
        else cell.append(el('p','沿用共同任务方案；本轮没有单独记录人格调整依据。','sim-small'));
        pair.append(cell);
      }row.append(pair);
      for(const event of events){row.append(el('p',eventFeedback(event),'inline-feedback'),traceFor(event));}
      if(current&&index===session.cursor){controls.hidden=false;row.append(controls);}
      if(current&&current.capability==='entertainment'&&['内容搜索','内容详情查看'].includes(current.name)&&events.some(e=>e.response.code==='OK')){
        const candidates=session.context.media_candidates||[];
        if(candidates.length){const choices=el('div',undefined,'inline-candidates');choices.append(el('p','查询返回的候选；选择播放属于用户手动追加操作。','sim-small'));
          for(const item of candidates){const itemRow=el('div',undefined,'media-choice');itemRow.append(el('span',`${item.name} · ${item.type} · ${item.duration_seconds}秒`));const button=el('button','选择并模拟播放');button.type='button';button.addEventListener('click',()=>run({manual_content:item.id,confirmed:true}));itemRow.append(button);choices.append(itemRow);}row.append(choices);}
      }
      root.append(row);
    }
    const manual=session.events.filter(e=>e.request.source==='user_manual');
    if(manual.length){const row=el('article',undefined,'behavior-step');row.append(el('h3','用户手动追加 · 选择与播放'),el('p','这些调用来自你的操作，不计为 ESTJ / INFP 的人格差异。','sim-small'));for(const event of manual)row.append(el('p',eventFeedback(event),'inline-feedback'),traceFor(event));root.append(row);}
    if(!ids.length)root.append(el('p','本轮没有生成原子能力动作。'));
  }
  async function run(extra={}){
    if(busy||!session)return;message('');lock(true);
    try{
      const body={revision:session.revision,...extra};
      if(!extra.manual_content){body.params=extra.params||Object.fromEntries([...$('arguments').querySelectorAll('input,select')].map(n=>[n.name,n.value]));body.confirmed=session.pending?.needs_confirmation===true;body.fault=$('fault').value;}
      session=await api(`/api/soa-sim/sessions/${session.id}/step`,body);render();
      const last=session.events.at(-1);if(last?.response.code!=='OK')message(last?.response.message||'模拟服务返回失败');
    }catch(e){message(e.message);}finally{lock(false);}
  }
  $('step-form').addEventListener('submit',e=>{e.preventDefault();run();});
  $('reset').addEventListener('click',()=>load(true));
  document.querySelectorAll('[data-persona]').forEach(button=>button.addEventListener('click',()=>{if(busy)return;persona=button.dataset.persona;query.set('persona',persona);history.replaceState(null,'','/soa-sim?'+query.toString());load();}));
  load();
})();
