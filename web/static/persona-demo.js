(() => {
'use strict';const $=id=>document.getElementById(id),el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=tag==='pre'?text:DemoDisplay.text(text);if(cls)n.className=cls;return n;};
let busy=false,caseId=null,timer=null,started=0,examples={},lastJob=null;
const explanations={},explanationPending=new Set(),traceSelection={},traceOpen={},differenceFilter={};
const autoScrolledJobs=new Set();
const modelContext=JSON.parse($('model-context').textContent),isKev=modelContext.backend==='kev';
const scenarioMetadata=JSON.parse($('scenario-metadata').textContent);
const {parameterDifferences,factorsFor,summary:actionSummary}=DemoFactors;
const {comparison,visibleActions,actionDifferences}=DemoContrast;
function lock(v){busy=v;$('generate').disabled=v;$('scene').disabled=v;document.querySelectorAll('[data-case]').forEach(n=>n.disabled=v);}
function error(s){$('error').textContent=s;$('error').hidden=!s;}
function loadExplanation(job){
 if(!job.id||job.status!=='completed'||job.id in explanations||explanationPending.has(job.id))return;
 explanationPending.add(job.id);
 fetch(`/api/persona-demo/${job.id}/explanation`).then(r=>{if(!r.ok)throw new Error('记录未就绪');return r.json();}).then(data=>{explanations[job.id]=data;if(lastJob?.id===job.id)render(lastJob);}).catch(()=>{explanations[job.id]={};if(lastJob?.id===job.id)render(lastJob);}).finally(()=>explanationPending.delete(job.id));
}
function personalityProfile(pid,meta,loaded){
 const box=el('div',undefined,'personality-profile');
 if(!meta){box.append(el('p',loaded?'本轮未保存人格配置，可查看动作中的模型理由。':'正在读取本轮人格配置…','profile-subtle'));return box;}
 const p=meta.profile||{},scene=meta.scene_strategy||{};
 box.append(el('p',`${p.dominant||''} ${p.dominant_name||''} · ${scene.action_strategy||''}`,'profile-title'),el('p',scene.cross_scenario_prototype||p.judgment_style||''));
 const more=el('details');more.append(el('summary','行为特征与判断逻辑'),el('p',p.persona_description||''),el('p',p.judgment_style||''),el('p','当前场景：'+(scene.scenario_strategy||'依当前任务组织行动')));
 const rules=meta.general_strategy?.action_logic||[];for(const rule of rules)more.append(el('p','· '+rule));
 more.append(el('p','来源：本轮保存的人格画像、功能策略与场景策略。属于项目设计假设，不代表每个真实个体。','profile-subtle'));box.append(more);return box;
}
function factorChips(action,meta){
 const wrap=el('div',undefined,'factor-chips'),factors=factorsFor(action,meta);
 if(!action)wrap.append(el('span','未采用，未记录该动作理由','factor-empty'));
 else if(!factors.length)wrap.append(el('span',/KEV独立判断/.test(action.why||'')?'独立因素判断，未记录动作因果':/人格未改变/.test(action.why||'')?'记录：人格未改变此动作':'未明确引用交互维度','factor-empty'));
 for(const f of factors){const peer=explanations[lastJob?.id]?.[meta?.persona==='ESTJ'?'INFP':'ESTJ'];const common=(peer?.dimensions||[]).some(d=>d.dimension===f.dimension&&d.description===f.description);const chip=el('span',f.label+(common?' · 共同规则':''),'factor-chip '+f.relation+(common?' common':''));chip.title=f.description;wrap.append(chip);}return wrap;
}
function differenceList(job,variants){
 const rows=actionDifferences(variants).filter(d=>d.kind!=='same'),root=el('section',undefined,'difference-list');
 const header=el('div',undefined,'difference-list-head');header.append(el('h3',`全部差异 · ${rows.length} 个对照项`));root.append(header);
 root.append(el('p','按保存的动作、参数和描述对照；字段不同不一定表示推荐效果不同。点一行查看两边的依据。','trace-legend'));
 const filters=el('div',undefined,'difference-filters'),selected=differenceFilter[job.id]||'all';
 for(const [kind,label] of [['all','全部'],['presence','动作采用'],['parameters','参数'],['description','仅描述']]){
   const count=kind==='all'?rows.length:rows.filter(d=>d.kind===kind).length,button=el('button',`${label} ${count}`);button.type='button';button.setAttribute('aria-pressed',String(selected===kind));button.addEventListener('click',()=>{differenceFilter[job.id]=kind;render(job);});filters.append(button);
 }root.append(filters);
 const scroller=el('div',undefined,'difference-scroll'),table=el('table'),thead=el('thead'),headrow=el('tr');for(const title of ['差在哪里','ESTJ · 做法与因素','INFP · 做法与因素'])headrow.append(el('th',title));thead.append(headrow);table.append(thead);const body=el('tbody');
 for(const d of rows.filter(d=>selected==='all'||d.kind===selected)){
   const tr=el('tr',undefined,traceSelection[job.id]===d.key?'selected-difference':''),name=el('td'),button=el('button',d.name,'difference-row-button');button.type='button';button.addEventListener('click',()=>{traceSelection[job.id]=d.key;traceOpen[job.id]=true;render(job);document.querySelector('.decision-view')?.scrollIntoView({block:'nearest',behavior:'smooth'});});name.append(button,el('span',/搜索|详情|查询|获取|筛选|比较/.test(d.name)?'查询准备':'结果或控制','scope-note'),el('span',d.kind==='presence'?'动作采用不同':d.kind==='parameters'?'参数不同':'仅描述不同','difference-kind'));tr.append(name);
   const keys=parameterDifferences(d.left,d.right);
   for(const [pid,a] of [['ESTJ',d.left],['INFP',d.right]]){const cell=el('td');cell.append(el('p',actionSummary(a,keys,d.kind),'difference-cell-summary'),factorChips(a,explanations[job.id]?.[pid]));tr.append(cell);}body.append(tr);
 }table.append(body);scroller.append(table);root.append(scroller);
 if(!body.children.length)root.append(el('p','此类别没有差异。','trace-legend'));
 root.append(el('p','“明确提及”来自该动作的模型解释；“文本关联”按用词匹配，仅是查看线索。未记录的原因不会补写。','trace-legend'));
 return root;
}
function decisionView(job,variants){
 const differences=actionDifferences(variants).filter(x=>x.kind!=='same'),meta=explanations[job.id];
 const box=el('details',undefined,'decision-view');box.open=Boolean(traceOpen[job.id]);box.addEventListener('toggle',()=>{traceOpen[job.id]=box.open;});box.append(el('summary',isKev?'查看「情境 → 人格配置 → KEV判断与动作」':'为什么会这样？查看「情境 → 性格 → 取舍 → 动作」'));
 box.append(el('p','绿色表示本轮动作或理由不同；灰色表示输入或配置。图中依据来自保存记录，不是心理因果证明。','trace-legend'));
 const shared=el('div',undefined,'shared-node');shared.append(el('span','共同输入与边界'),el('p',job.input||''),el('p','两种人格共享相同任务与安全边界；安全优先本身不计为人格差异。','trace-legend'));const world=el('details');world.append(el('summary','共同虚拟设定'),el('p',job.world||'本轮未保存'));shared.append(world);box.append(shared);
 if(!meta){box.append(el('p','本轮配置记录暂不可用，动作与理由仍可在完整行为中查看。'));return box;}
 const chosen=traceSelection[job.id]||comparison(variants)?.rows[0]?.find?.(a=>a&&typeof a==='object');
 let selected=differences.find(x=>x.key===chosen)||differences.find(x=>x.left===chosen||x.right===chosen)||differences[0];
 if(!selected){box.append(el('p','本轮动作与参数一致，没有可高亮的动作分歧。'));return box;}
 box.append(el('h4','当前查看：'+selected.name,'selected-trace-title'));
 const grid=el('div',undefined,'trace-pair');
 for(const [pid,a] of [['ESTJ',selected.left],['INFP',selected.right]]){
   const info=meta[pid]||{},p=info.profile||{},scene=info.scene_strategy||{},column=el('section',undefined,'trace-column');column.append(el('h4',pid));
   const nodes=[['人格关注点',scene.cross_scenario_prototype||p.judgment_style||'本轮未保存'],[isKev?'并行因素判断（非动作原因）':'本轮取舍依据',a?[a.why,a.basis].filter(Boolean).join('；'):'本轮未采用该动作，未记录这项动作的取舍理由。'],['具体动作',a?.text||'本轮未采用这项动作']];
   for(const [label,text] of nodes){const node=el('div',undefined,'decision-node'+(label==='人格关注点'?' configured':' highlighted'));node.append(el('span',label),el('p',text));if(label==='本轮取舍依据'||label==='并行因素判断（非动作原因）'){node.append(factorChips(a,info));for(const factor of factorsFor(a,info)){const source=el('details');source.append(el('summary',factor.label+' · 查看本轮配置'),el('p',factor.description),el('p','配置位置：'+(factor.function_text_source||'本轮Step 6交互策略')));node.append(source);}}if(label==='具体动作'&&a&&Object.keys(a.parameters||{}).length){const args=el('details');args.append(el('summary','具体参数'),el('pre',JSON.stringify(a.parameters,null,2)));node.append(args);}column.append(node);}grid.append(column);
 }
 box.append(grid,el('p',selected.kind==='presence'?'差异类型：是否采用该动作不同。':selected.kind==='parameters'?'差异类型：实际动作参数不同。':'差异类型：具体描述不同；不能仅据此断言调用效果不同。','trace-legend'));
 const evidence=el('details');evidence.append(el('summary','查看本轮交互策略与共同约束'));
 const dims=new Set(['ESTJ','INFP'].flatMap(p=>(meta[p]?.dimensions||[]).map(d=>d.dimension)));
 for(const name of dims){const left=meta.ESTJ?.dimensions.find(d=>d.dimension===name),right=meta.INFP?.dimensions.find(d=>d.dimension===name),same=left?.description===right?.description;const row=el('div',undefined,'policy-row'+(same?'':' policy-different'));row.append(el('strong',name+(same?' · 共同要求':' · 配置有差异')));for(const [pid,d] of [['ESTJ',left],['INFP',right]])row.append(el('p',pid+'：'+(d?.description||'本轮未启用')));evidence.append(row);}box.append(evidence);const assumptions=el('details');assumptions.append(el('summary','各自补充的演示假设（不直接算作人格差异）'));for(const pid of ['ESTJ','INFP']){assumptions.append(el('strong',pid));for(const note of variants[pid]?.assumptions||[])assumptions.append(el('p',note));}box.append(assumptions);return box;
}
function render(job){
 lastJob=job;const scenario=scenarioMetadata.find(c=>c.id===job.case_id);$('scenario-tip').textContent=scenario?`${scenario.why_suitable} 观察：${scenario.observe}。`:'自由情境也可对照；演示假设或未知信息可能影响结果，不能都归因于人格。';document.querySelectorAll('[data-case]').forEach(n=>n.setAttribute('aria-pressed',String(n.dataset.case===job.case_id)));$('generate').textContent=job.status==='running'?'正在生成…':'重新生成 →';const root=$('results');root.replaceChildren();const variants=job.result?.variants||job.variants||{};loadExplanation(job);const differences=actionDifferences(variants);
 const head=el('div',undefined,'result-heading');head.append(el('h2',job.status==='failed'?'本次生成未完成':job.status==='completed'?'两种性格，具体怎么做。':job.stage==='preparing'?'正在准备情境、候选和车机回复…':job.stage==='understanding'?'正在理解共同情境…':job.stage==='wording'?'正在将已选动作写成车机回复…':job.stage==='capabilities'?'正在筛选相关能力并编译依赖…':job.stage==='candidates'?'正在准备共同候选…':isKev?'本地 KEV 正在决策…':'两种人格正在独立推演…'),el('span',job.status==='failed'?`${job.elapsed_seconds??'—'} 秒 · 已停止`:job.result?.replay_of?`已存结果回放 · 原记录 ${job.result.original_elapsed_seconds} 秒`:job.result?`${job.result.elapsed_seconds} 秒 · ${modelContext.label}`:`已等待 ${Math.floor((Date.now()-started)/1000)} 秒`));root.append(head);if(job.status==='completed')$('source-label').textContent='本轮已完成';if(job.status==='failed')$('source-label').textContent='本次已停止';if(job.status==='failed'){root.append(el('p',job.error||'本次未完成，请重试。','failure'));$('audit-link').hidden=true;return;}if(isKev&&job.status==='completed')root.append(el('p',job.result?.contrast?.disclosure||'KEV 直接选择候选；话术由程序组织。分数接近与相同结果如实保留，因素判断不代表动作因果。','trace-legend'));
 if(isKev&&job.status==='completed')root.append(el('p',job.result?.contrast?.input_version?`本轮 KEV 输入：${job.result.contrast.input_version} · 情境＋约束＋角色＋${job.result.mode==='kev_free_atomic_v2'?'原子能力机会':'候选'}`:'这是旧版输入的已存记录；点击重新生成，使用精简四块输入。','trace-legend'));
 if(isKev&&job.result?.mode==='kev_free_bundles')root.append(el('p','这是旧版自由输入记录：下方为方案折算分值。重新生成后会使用统一行为对照与57项独立评分，旧分值不会被伪装为独立评分。','trace-legend'));
 if(job.result?.mode==='kev_free_atomic_v2'&&!job.result?.replay_of)root.append(el('p',`耗时：中立理解 ${job.result.intent_seconds} 秒${job.result.intent_cached?'（缓存）':''} · 本地评分 ${job.result.decision_seconds} 秒。`,'trace-legend'));
 if(job.result?.mode==='kev_free_atomic'){const r=job.result,c=r.candidate_preparation;const timing=c.source==='single_neutral_call_and_recipe_compiler'?`耗时拆分：共享准备（理解、候选与话术）${c.seconds} 秒${c.cache_hit?'（复用共享准备）':''} · 本地 KEV 评分 ${r.decision_seconds} 秒。`:`耗时拆分：理解 ${r.intent_seconds} 秒 · 候选准备 ${c.seconds} 秒 · 本地决策 ${r.decision_seconds} 秒。`;root.append(el('p',timing,'trace-legend'));}
 if(Object.values(variants).some(v=>v.status==='completed')){const replies=el('section',undefined,'spoken-responses');replies.append(el('h3','车机会这样回应','spoken-title'));const columns=el('div',undefined,'spoken-pair');for(const p of ['ESTJ','INFP']){const column=el('article',undefined,'spoken-column');column.append(el('strong',p),el('p',variants[p]?.reply||'正在组织回应…','spoken-reply'));
const basis=variants[p]?.selection_basis||variants[p]?.bundle_choice?.selection_basis;if(basis?.criterion){column.append(el('p','选择标准：'+basis.criterion,'spoken-basis'));if(basis.facts?.length)column.append(el('p','本轮演示信息：'+basis.facts.join('；'),'spoken-basis'));column.append(el('p','为什么选：'+basis.reason,'spoken-basis'));if(basis.tradeoff)column.append(el('p','取舍：'+basis.tradeoff,'spoken-basis'));} columns.append(column);}replies.append(columns);root.append(replies);}
 if(job.result?.replay_of)root.append(el('p','已存模型结果的校验回放，未发起新在线请求；原记录 '+job.result.replay_of+'。当前API账户额度不足。','trace-legend'));
 if(job.result?.wording){const w=job.result.wording;root.append(el('p',['cloud_after_frozen_decision','saved_cloud_validated'].includes(w.source)?w.source==='saved_cloud_validated'?'表达来源：已存云端回复，经过动作一致性校验；本次没有调用云端模型。':`最终表达：${w.model} · ${w.seconds} 秒 · 只表达已选结果，不重新决策。`:w.notice||'当前使用本地事实回复。','trace-legend'));}
 if(job.result?.variants){const resources=KevAtomic.renderResources(job.result);if(resources)root.append(resources);}
 if(isKev&&job.result?.contrast?.unified_role_rules)root.append(KevAtomic.renderRoleDecision(variants));
 if(job.result?.mode==='kev_free_atomic_v2'&&!job.result?.replay_of)root.append(KevAtomic.renderPipeline(job.result));
 const atomicScope=job.result?.atomic_scope||job.atomic_scope;const strongContrast=['strong_contrast','atomic_comparison'].includes(job.result?.contrast?.mode);if(strongContrast){root.append(KevAtomic.renderContrast(job.result.contrast,variants));if(atomicScope.mode==='atomic_bundle_contrast')root.append(KevAtomic.render(atomicScope,variants,job.id||'running'));else{const atoms=el('details');atoms.append(el('summary',`查看 ${atomicScope.rows.length} 项潜在原子能力与原始方案折算倾向`),KevAtomic.render(atomicScope,variants,job.id||'running'));root.append(atoms);}}else if(isKev&&atomicScope)root.append(KevAtomic.render(atomicScope,variants,job.id||'running'));else if(isKev&&job.status==='completed')root.append(el('p','这条记录来自旧版决策；点击重新生成，查看本情境的逐项采用概率。','trace-legend'));
 const pair=el('div',undefined,'pair');
 for(const pid of ['ESTJ','INFP']){const v=variants[pid],card=el('article',undefined,'persona'),top=el('div',undefined,'persona-top');top.append(el('h2',pid),el('span',v?.strategy||(isKev?'KEV 本地决策':'完整人格链路')));card.append(top);if(v?.status==='completed')card.append(personalityProfile(pid,explanations[job.id]?.[pid],job.id in explanations));
  if(!v){card.append(el('p','正在生成具体安排与车机回应…','waiting'),el('div',undefined,'skeleton'));}
  else if(v.status==='failed')card.append(el('p',v.error,'failure'));
  else{card.append(el('p','车机会这样说','label'),el('p',v.reply,'reply'));if(v.decisions){const scores=Object.values(v.decisions.find(d=>d.id==='selection')?.scores||{}).sort((a,b)=>b-a);if(scores.length>1&&scores[0]-scores[1]<0.05)card.append(el('p','候选分数接近，本轮选择可能对措辞与顺序敏感。','trace-legend'));}card.append(el('p','具体行动','label'));const list=el('ol',undefined,'actions');for(const [i,a] of visibleActions(v.actions).entries()){const changed=differences.find(d=>(pid==='ESTJ'?d.left:d.right)?.order===a.order);const li=el('li',undefined,changed&&changed.kind!=='same'?'action-changed':'');if(changed&&changed.kind!=='same')li.setAttribute('aria-label',changed.kind==='presence'?'采用动作不同':changed.kind==='parameters'?'参数不同':'动作描述不同');li.append(el('span',String(i+1).padStart(2,'0'),'number'),el('p',a.text));if(changed&&changed.kind!=='same')li.lastElementChild.append(el('span',changed.kind==='presence'?'动作采用不同':changed.kind==='parameters'?'参数不同':'描述不同','delta-badge'));list.append(li);}card.append(list);
   const distinctive=visibleActions(v.actions).find(a=>a.why&&!/人格未改变|^无人格|^仅情境/.test(a.why))||v.actions.find(a=>a.why&&!/人格未改变/.test(a.why))||v.actions[0];if(distinctive){const why=el('div',undefined,'why');why.append(el('strong',isKev?(v.bundle_choice?'本轮完整方案与角色策略':'KEV 的独立因素判断'):'为什么这样做'),el('p',distinctive.why));card.append(why);}
   const full=el('details');full.append(el('summary',`完整行为与依据 · ${v.actions.length} 个原子动作`));for(const a of v.actions)full.append(el('p',`${a.order}. ${a.name}：${a.text}\n依据：${a.why}；${a.basis}`));full.append(el('p','本轮交互维度：'+v.dimensions.join('、')));card.append(full);if(v.decisions){const scores=el('details');scores.append(el('summary','KEV 本轮选项与分数'));for(const d of v.decisions){scores.append(el('strong',d.question),el('p',(v.bundle_choice?'KEV原始首选：':'本轮选择：')+d.label));for(const [key,value] of Object.entries(d.scores))scores.append(el('p',`${d.options[key]}：${(value*100).toFixed(1)}%${key===d.choice?' · 选中':''}`));}scores.append(el('p','分数是模型输出，不是心理因素的因果权重。','trace-legend'));card.append(scores);}
  }pair.append(card);
 }if(strongContrast||job.result?.mode==='kev_free_atomic_v2'){const details=el('details');details.append(el('summary','展开完整原子动作与依据'),pair);root.append(details);}else root.append(pair);
 const diff=comparison(variants);if(diff&&!strongContrast){const box=el('section',undefined,'difference');box.append(el('h3',diff.rows.length?'最值得看的差异':'本轮做法接近，没有强行制造差异'));
  if(diff.matching)box.append(el('p','原子能力与参数组合相同；以下对照具体安排和表达，不代表调用了不同服务。'));
  for(const [name,l,r] of diff.rows){const grid=el('div',undefined,'diff-grid');for(const [pid,a] of [['ESTJ',l],['INFP',r]]){const cell=el('div');const selected=/目的地设置|内容选择|候选方案选择/.test(name)?(a?.parameters?.POI||a?.parameters?.内容||a?.parameters?.候选项):null;const label=selected&&!/前序|返回|已选/.test(String(selected))?String(selected).replace(/^[RMP]\d+\s*/, ''):null;cell.append(el('strong',pid+' · '+(label?'选择结果':name)),el('p',label||a?.text||'本轮未采用这项动作'));grid.append(cell);}box.append(grid);}if(isKev&&atomicScope){const more=el('details');more.append(el('summary','查看动作参数差异与人格配置依据'),differenceList(job,variants),decisionView(job,variants));box.append(more);}else box.append(differenceList(job,variants),decisionView(job,variants));root.insertBefore(box,root.querySelector('.atomic-panel')||pair);
 }
 if(job.result?.compiled_from_saved_output)$('source-label').textContent='已存模型结果 · 公共查询依赖由程序补齐并通过校验';
 $('world-box').hidden=true;$('world').textContent=job.world;$('audit-link').hidden=job.status!=='completed';$('audit-link').href=`/api/persona-demo/${job.id}/audit`;DemoDisplay.apply(document.body);
 if(isKev&&atomicScope&&job.status==='completed'&&job.id&&!autoScrolledJobs.has(job.id)){
  autoScrolledJobs.add(job.id);
  requestAnimationFrame(()=>root.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'}));
 }
}
async function poll(id){try{const res=await fetch('/api/persona-demo/'+id);const job=await res.json();if(!res.ok)throw new Error(job.error);render(job);if(job.status==='failed'){error(job.error);lock(false);return;}if(job.status==='completed'){lock(false);return;}timer=setTimeout(()=>poll(id),900);}catch(e){render({status:'failed',error:e.message,elapsed_seconds:Math.floor((Date.now()-started)/1000),variants:{}});error(e.message);lock(false);}}
async function start(){if(busy)return;error('');lock(true);started=Date.now();$('source-label').textContent=isKev?'正在理解中立需求，随后筛选能力并由本地 KEV 决策。':'正在实时生成，未复用已存结果。';render({status:'running',stage:'understanding',variants:{},world:'正在准备共享的演示世界…'});try{const res=await fetch('/api/persona-demo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:$('scene').value,case_id:caseId,model:modelContext.backend})});const data=await res.json();if(!res.ok)throw new Error(data.error);history.replaceState(null,'',modelContext.path+'?job='+data.id);poll(data.id);}catch(e){render({status:'failed',error:e.message,elapsed_seconds:Math.floor((Date.now()-started)/1000),variants:{}});error(e.message);lock(false);}}
function saveDraft(){sessionStorage.setItem('persona-demo-draft',JSON.stringify({text:$('scene').value,caseId}));}
document.querySelectorAll('.model-nav a[href^="/demo"]').forEach(a=>a.addEventListener('click',saveDraft));
try{const draft=JSON.parse(sessionStorage.getItem('persona-demo-draft'));if(draft&&!new URLSearchParams(location.search).get('job')){$('scene').value=draft.text||'';caseId=draft.caseId||null;}}catch{}
$('demo-form').addEventListener('submit',e=>{e.preventDefault();start();});$('scene').addEventListener('input',()=>{caseId=null;document.querySelectorAll('[data-case]').forEach(n=>n.setAttribute('aria-pressed','false'));showDraft();});
function showDraft(){clearTimeout(timer);error('');lastJob=null;$('source-label').textContent='尚未运行，点击开始对比后生成。';$('generate').textContent='开始对比 →';$('results').replaceChildren(el('p','情境已就绪，点击“开始对比”再生成结果。','trace-legend'));$('world-box').hidden=true;$('audit-link').hidden=true;history.replaceState(null,'',modelContext.path);DemoDisplay.apply(document.body);}
document.querySelectorAll('[data-case]').forEach(n=>n.addEventListener('click',()=>{if(busy)return;caseId=n.dataset.case;$('scene').value=n.dataset.text;document.querySelectorAll('[data-case]').forEach(x=>x.setAttribute('aria-pressed',String(x===n)));const selected=scenarioMetadata.find(c=>c.id===caseId);$('scenario-tip').textContent=selected?selected.why_suitable+' 观察：'+selected.observe:'';showDraft();saveDraft();}));
const jid=new URLSearchParams(location.search).get('job');if(jid&&/^[a-f0-9]{32}$/.test(jid)){started=Date.now();fetch('/api/persona-demo/'+jid).then(r=>r.json()).then(j=>{if(j.error)throw new Error(j.error);if((j.model||'original')!==modelContext.backend)throw new Error('这条记录来自另一个模型，请切换模型入口查看。');$('scene').value=j.input;caseId=j.case_id;$('source-label').textContent='已保存的实际生成记录';if(j.status==='running')lock(true);render(j);if(j.status==='running')poll(jid);}).catch(e=>error(e.message));}
DemoDisplay.apply(document.body);
})();
