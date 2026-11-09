(function(root){
'use strict';
const make=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=tag==='pre'?text:(root.DemoDisplay?.text(text)??text);if(cls)n.className=cls;return n;};
const preferences=new Map();
function compared(scope,variants){
 const maps=Object.fromEntries(['ESTJ','INFP'].map(p=>[p,new Map((variants[p]?.atomic_scores||[]).map(s=>[s.id,s]))]));
 return scope.rows.map(r=>{const l=maps.ESTJ.get(r.id),v=maps.INFP.get(r.id);return {...r,left:l,right:v,
  differs:!!l&&!!v&&l.adopted!==v.adopted,delta:Number.isFinite(l?.probability)&&Number.isFinite(v?.probability)?l.probability-v.probability:null};});
}
function render(scope,variants,jobId){
 const direct=scope.mode==='atomic_direct',independent=scope.mode==='atomic_bundle_contrast'||direct;
 const bundled=scope.mode==='bundle_contrast'||scope.mode==='bundle_selection';
 const rows=compared(scope,variants),section=make('section',undefined,'atomic-panel');
 const title=make('div',undefined,'atomic-heading');title.append(make('h3',independent?`原子能力采用倾向 · 全部 ${rows.length} 项`:`本情境潜在原子能力 · ${rows.length} 项`));
 const counts=['ESTJ','INFP'].map(p=>variants[p]?.atomic_scores?`${p} 采用 ${variants[p].atomic_scores.filter(s=>s.adopted).length} 项`:`${p} 等待决策`);
 title.append(make('span',counts.join(' / '),'atomic-counts'));section.append(title);
 section.append(make('p',independent?'主项展示最终采用与决定原因，下方保留 KEV 原始参考分（0–100）。参考分不是最终执行概率，也不是50分执行阈值。':bundled?`本轮 KEV 比较 ${scope.bundle_count} 个完整方案。逐项数值由原始方案分布求和折算，不是对原子能力单独询问的置信度；未进入方案的项目不评分。`:`已逐项核对能力库 ${scope.total_catalog} 项；本轮 ${scope.scored_count} 项由 KEV 判断，必要前置与共同任务单独标注。每行独立，百分比不按列相加。`,'trace-legend'));
 section.append(make('p',direct?'可选末端动作按综合优先分≥50采用；该分由原始参考分与公开角色收益计算。共同必需动作和依赖单独标明，未进入中立能力池的项目不追加。':independent?'分值参与完整方案匹配度排序，再应用角色约束；高分不保证执行。分值尚未经过校准，不代表真实用户群体概率。':bundled?'绿色表示最终选中完整方案包含此项。原始方案分数不修改，角色策略可能改变最终选择，相关调整单独标注。':'绿色表示模拟计划实际采用；“依赖补齐”可能覆盖模型的不采用建议，并保留原分数。参数是共同候选假设，概率尚未做人格场景校准。','trace-legend'));
 const controls=make('div',undefined,'difference-filters'),scroller=make('div',undefined,'atomic-scroll');
 section.append(controls,scroller);
 const options=[['all',independent?'全部能力':'全部潜在',rows.length],...(independent?[['relevant','本场景适用',rows.filter(r=>r.applicable).length]]:[]),['different','采用不同',rows.filter(r=>r.differs).length],
  ['adopted','至少一方采用',rows.filter(r=>r.left?.adopted||r.right?.adopted).length],
  ['prerequisite','前置与共同步骤',rows.filter(r=>['prerequisite','core'].includes(r.role)).length]];
 let filter=preferences.get(jobId)||(direct?'relevant':'all');
 function draw(){
  controls.replaceChildren();scroller.replaceChildren();
  for(const [id,label,count] of options){const b=make('button',`${label} ${count}`);b.type='button';b.setAttribute('aria-pressed',String(filter===id));b.onclick=()=>{filter=id;preferences.set(jobId,id);draw();};controls.append(b);}
  const table=make('table'),head=make('thead'),hr=make('tr');
  for(const t of ['潜在原子能力',independent?'ESTJ · 最终决定／原始参考分':bundled?'ESTJ · 方案折算倾向／最终采用':'ESTJ · 采用概率与决定',independent?'INFP · 最终决定／原始参考分':bundled?'INFP · 方案折算倾向／最终采用':'INFP · 采用概率与决定',independent?'参考分差':bundled?'折算倾向差':'概率差']){const th=make('th',t);th.scope='col';hr.append(th);}head.append(hr);table.append(head);
  const body=make('tbody');
  const shown=rows.filter(r=>filter==='all'||filter==='relevant'&&r.applicable||filter==='different'&&r.differs||filter==='adopted'&&(r.left?.adopted||r.right?.adopted)||filter==='prerequisite'&&['prerequisite','core'].includes(r.role))
    .sort((a,b)=>Number(b.differs)-Number(a.differs)||Number(b.applicable!==false)-Number(a.applicable!==false)||Math.abs(b.delta||0)-Math.abs(a.delta||0));
  for(const r of shown){
   const tr=make('tr',undefined,(r.differs?'atomic-different ':'')+(r.applicable===false?'atomic-outside':'')),name=make('th');name.scope='row';name.append(make('strong',r.name),make('span',r.domain_label+' · '+(r.applicable===false?'本场景不适用':r.role==='candidate'?'可选动作':r.role==='core'?'共同任务':'必要前置'),'atomic-note'));name.title=r.definition+'；'+(bundled?'本轮按共享完整方案是否包含此项展示':r.reason);tr.append(name);
   for(const s of [r.left,r.right]){const td=make('td',undefined,s?.adopted?'atomic-adopted':'atomic-omitted');
    if(!s){td.append(make('span','等待结果','atomic-note'));}
    else if(s.probability!==null){
     if(independent){
      const top=make('div',undefined,'atomic-value');top.append(make('strong',s.adopted?'采用':'不采用'),make('span',s.decision_source||'按完整方案选择','atomic-status'));td.append(top);
      td.append(make('p',s.decision_reason||s.resolution,'atomic-note'));if(Number.isFinite(s.priority_score))td.append(make('strong','综合优先分 '+(s.priority_score*100).toFixed(1)+' / 100'));td.append(make('span','KEV 原始参考分 '+(s.probability*100).toFixed(1)+' / 100','atomic-note'));
     }else{
      const top=make('div',undefined,'atomic-value');top.append(make('strong',`${(s.probability*100).toFixed(1)}%`),make('span',s.adopted?'采用':'不采用','atomic-status'));td.append(top);
      td.append(make('span',s.resolution,'atomic-note'));
     }
    }else{td.append(make('strong',s.adopted?'采用':'未采用'),make('span','— 未独立评分','atomic-note'),make('span',s.resolution,'atomic-note'));}
    tr.append(td);
   }
   const delta=make('td',r.delta===null?'—':`${r.delta>0?'+':''}${(r.delta*100).toFixed(1)} ${independent?'分':'pp'}`,'atomic-delta');delta.title='ESTJ 减 INFP；模型原始参考分差，不是最终决策分差或人格因果权重';tr.append(delta);body.append(tr);
  }
  table.append(body);scroller.append(table);if(!shown.length)scroller.append(make('p','此分类暂无对照项。','trace-legend'));
 }
 draw();
 if(independent){section.append(make('p',direct?'排序：采用不同 → 本场景适用 → 原始分差。可选动作采用阈值作用于公开规则综合优先分，不能套用到模型原始分或必要前置。':'排序：采用不同 → 本场景适用 → 分值差较大。整条方案同时满足依赖和角色边界，不按单项50%阈值拼接。','trace-legend'));return section;}
 const excluded=make('details');excluded.append(make('summary',`其余 ${scope.excluded.length} 项为什么没有进入候选？`));
 for(const r of scope.excluded)excluded.append(make('p',`${r.domain_label} / ${r.name}：${r.reason}`));
 excluded.append(make('p','候选范围来自共同情境与覆盖规则，不按人格预先删选；更换任务后会重新筛选。','trace-legend'));section.append(excluded);
 return section;
}
function inputPreview(variants){
 const runtime=variants.ESTJ?.bundle_choice?.runtime_input;if(!runtime)return null;
 const box=make('details',undefined,'compact-input-preview');box.append(make('summary','查看本轮 KEV 四块输入 · '+variants.ESTJ.bundle_choice.input_version),make('h4','1. 本轮情境'),make('pre',JSON.stringify(runtime.situation,null,2)),make('h4','2. 共同约束'),make('pre',JSON.stringify(runtime.constraints,null,2)),make('h4','3. 精简角色规则'));
 for(const p of ['ESTJ','INFP']){const role=variants[p].bundle_choice.runtime_input.role;box.append(make('strong',p));for(const rule of role.rules)box.append(make('p',rule));if(role.authored_demo_scope)box.append(make('p','附加演示约束：'+role.authored_demo_scope.rule));}
 box.append(make('h4','4. 共享候选'));for(const [id,c] of Object.entries(runtime.candidates))box.append(make('p',id+'：'+c.outcome),make('p','动作：'+c.actions.join('、')));
 box.append(make('p','以上是实际送入KEV的state。57项能力分别提问，问题不读取彼此答案；原始人格全文只保存在审计来源中。','trace-legend'));return box;
}
function renderRoleDecision(variants){
 const box=make('section',undefined,'role-decision');box.append(make('h3','为什么采取不同的行动？'));
 const pair=make('div',undefined,'spoken-pair');
 for(const p of ['ESTJ','INFP']){
  const v=variants[p],c=v?.bundle_choice;if(!c)continue;
  const scope=c.scene_role_constraints,cell=make('article',undefined,'role-decision-column');
  cell.append(make('strong',p+' · '+(p==='ESTJ'?'目标、标准与收敛（Te / Si）':'感受、自主与探索（Fi / Ne）')));
  cell.append(make('p',scope?.goal||c.role_policy.goal));
  const rawName=v.decisions.find(d=>d.id==='bundle')?.options[c.atomic_preferred||c.raw_choice]||c.raw_choice;
  cell.append(make('p','原始评分匹配首选：'+rawName,'trace-legend'));
  cell.append(make('p','强化规则 → '+(scope?.applied?'保留 '+c.eligible.join('、'):'保留全部合法候选')));
  cell.append(make('p','最终采用：'+c.name,'role-selection'));
  if(scope?.applied){cell.append(make('p',scope.candidate_evidence[c.selected]||''));if(scope.candidate_adaptation)cell.append(make('p',scope.candidate_adaptation,'trace-legend'));}
  else cell.append(make('p',scope?.reason||'本轮未施加额外角色筛选。'));
  cell.append(make('small',(c.selection_effect||'按本轮评分选择')+'；原始分值未改写。','trace-legend'));pair.append(cell);
 }
 box.append(pair);return box;
}
function renderContrast(data,variants){
 const strong=data.mode==='strong_contrast',unified=data.unified_role_rules,box=make('section',undefined,'contrast-panel');box.append(make('span',unified?'统一强化角色 · 预设与自由输入同一规则':strong?'强对比角色演示 · 含人为策略约束':'自由情境 · 统一原子评分与行为对照','contrast-badge'),make('h3',data.meaningful_differences.length?`${data.meaningful_differences.length} 类${unified?'行为差异（采用或参数）':'实质采用分歧'}`:'本轮采用一致，分值与具体安排仍可比较'));
 box.append(make('p',strong?'先比较做了什么、没有做什么。灯光开关、颜色和亮度合并为一类，不重复计算。':'先看共同完成了什么、辅助动作有何取舍，再看原始分值。安全目标相同可以采用相同动作，不强行制造差异。','trace-legend'));if(data.candidate_action_sets===1)box.append(make('p','本轮共享候选的能力组合相同，采用差异空间有限；可继续比较地点、参数与分值。','trace-legend'));
 const table=make('table'),head=make('tr');for(const title of ['具体体验','ESTJ · '+variants.ESTJ.strategy.replace(/^ESTJ · /,'').split(' · ')[0],'INFP · '+variants.INFP.strategy.replace(/^INFP · /,'').split(' · ')[0]])head.append(make('th',title));table.append(head);
 for(const [label,domain,names,why] of (data.groups||[['通知管理','notification_mode',null],['灯光氛围','ambient_light',null],['身体放松','seat',null],['内容播放','entertainment',['播放控制']]])){
  const row=make('tr',undefined,data.meaningful_differences.includes(label)?'atomic-different':'');row.append(make('th',label));
  for(const p of ['ESTJ','INFP']){const actions=(variants[p]?.actions||[]).filter(a=>a.domain===domain&&(!names||names.includes(a.name))),cell=make('td',undefined,actions.length?'atomic-adopted':'atomic-omitted');
   const reasons={'通知管理':['先处理外部干扰，恢复掌控感。','本轮不整理事务，通知保持原设置。'],'灯光氛围':['避免额外环境调整，少量动作即可。','用柔和环境帮助体验恢复。'],'身体放松':['本轮以有边界的短休息为主。','让身体放松成为恢复体验的一部分。'],'内容播放':['优先选短而明确的内容安排。','优先选能帮助情绪恢复的音乐。']};cell.append(make('strong',actions.length?'采用':'不采用'),make('p',actions.length?actions.map(a=>a.text).join(''):['navigation','ecosystem'].includes(domain)?'不额外执行此项':'保持原设置'),make('small',(strong||unified?'角色规则：':'角色关注点：')+(label==='内容播放'&&!actions.length&&!why?'本轮选择安静休息，不追加内容播放。':(why||reasons[label])[p==='ESTJ'?0:1]),'contrast-reason'));if(variants[p].bundle_choice?.selection_method==='atomic_log_likelihood'){const scoreNames=names||({notification_mode:['通知静音控制','非紧急通知屏蔽','提醒延后'],ambient_light:['氛围灯开关','颜色设置','亮度调节'],seat:['座椅按摩控制','座椅位置调节']}[domain]);const scores=(variants[p].atomic_scores||[]).filter(a=>a.id.startsWith(domain+'/')&&(!scoreNames||scoreNames.includes(a.id.split('/')[1])));cell.append(make('p','决定依据：'+[...new Set(scores.map(a=>a.decision_source||'完整方案取舍'))].join('、'),'contrast-scores'));}row.append(cell);}table.append(row);
 }const outcome=make('tr');outcome.append(make('th','具体安排'));for(const p of ['ESTJ','INFP']){const td=make('td');td.append(make('p',variants[p].reply));outcome.append(td);}table.append(outcome);box.append(table);
 if(data.score_method)box.append(make('p',data.score_method,'trace-legend'));
 const preview=inputPreview(variants);if(preview)box.append(preview);
 const provenance=make('details');provenance.append(make('summary',strong?`查看差异来源与真实注入策略 · 本轮策略调整 ${data.policy_override_count} 次`:'查看真实注入策略与方案评分过程'));
 provenance.append(make('p',data.disclosure));if(preview)provenance.append(make('p','以下完整画像与强化文案为审计来源；运行时使用上方四块输入中的精简规则。','trace-legend'));
 for(const p of ['ESTJ','INFP']){const v=variants[p],c=v.bundle_choice,d=v.decisions.find(d=>d.id==='bundle'),policy=c.injected_role_policy||c.role_policy;provenance.append(make('strong',p+' · '+(c.policy_version||'旧版输入')));if(c.original_persona_profile){provenance.append(make('p','原始人格描述：'+c.original_persona_profile.persona_description),make('p','原始判断方式：'+c.original_persona_profile.judgment_style));}provenance.append(make('p','通用角色目标：'+policy.goal),make('p','优先事项：'+policy.priorities.join('；')));if(policy.cognitive_basis)provenance.append(make('p','功能依据：'+Object.entries(policy.cognitive_basis).map(([k,v])=>k+'：'+v).join('；')));if(policy.prefer_when)provenance.append(make('p','倾向采用条件：'+policy.prefer_when.join('；')));if(policy.avoid_when)provenance.append(make('p','倾向克制条件：'+policy.avoid_when.join('；')));provenance.append(make('p','停止条件：'+policy.stop));if(policy.interaction)provenance.append(make('p','演示交互：'+policy.interaction));if(c.scene_role_constraints)provenance.append(make('p','本轮人为角色约束：'+c.scene_role_constraints.goal+' '+c.scene_role_constraints.priorities.join('；')),make('p',c.scene_role_constraints.reason||''));provenance.append(make('p',(c.selection_method==='atomic_log_likelihood'?'方案单独评分首选（参考）：':'KEV原始首选：')+d.options[c.raw_choice]),...(c.atomic_preferred?[make('p','原子分值匹配首选：'+d.options[c.atomic_preferred])]:[]),make('p','策略内最终采用：'+c.name+(c.policy_overridden?'（原始首选不满足角色策略，按本轮评分选取可行方案）':'（评分排序后直接采用）')));}
 for(const p of ['ESTJ','INFP']){const c=variants[p].bundle_choice;if(c.atomic_fit_scores&&Object.keys(c.atomic_fit_scores).length){provenance.append(make('strong',p+' · 完整方案匹配度'));for(const [id,value] of Object.entries(c.atomic_fit_scores))provenance.append(make('p',id+' · '+(value*100).toFixed(1)+'%'+(id===c.selected?' · 最终采用':'')+(c.eligible.includes(id)?'':' · 超出角色范围')));}}
 provenance.append(make('p',unified?'KEV负责原始评分；统一强化规则限定角色的选择范围。范围仅剩一个方案时，由角色规则决定最终选择，页面如实标注。':'原始分值与最终采用分开展示，角色关注点不是内部推理或心理因果证明。','trace-legend'));box.append(provenance);return box;
}
function renderPipeline(result){
 const box=make('section',undefined,'role-decision');box.append(make('h3','这轮怎样理解与决策'));
 box.append(make('p','中立理解 → 相关能力 → KEV原始评分＋公开角色规则 → 依赖编排 → 根据动作组织回复','trace-legend'));
 const u=result.observation||{};
 for(const n of u.needs||[])box.append(make('p',(n.explicit&&u.task?.action_requested?'明确请求：':'情境推断：')+n.need+'；依据：'+n.evidence));
 box.append(make('p','共同目标：'+(u.task?.core_goal||result.input)),make('p','行驶状态：'+({driving:'正在驾驶',parked:'已驻车'}[u.context?.driving_state]||'未知，不擅自当作驻车')));
 const list=make('details');list.append(make('summary','相关能力 '+result.opportunity_pool.length+' 项 · 查看情境依据与排除原因'));
 for(const o of result.opportunity_pool)list.append(make('p',o.ability+' · '+(o.required?'共同必需':'可选支持')+'：'+o.reason));
 for(const o of result.rejected_opportunities||[])list.append(make('p','未纳入 '+o.ability+'：'+o.reason));
 box.append(list);
 const rules=make('details');rules.append(make('summary','查看角色规则与真实KEV输入'));
 rules.append(make('p','综合优先分 = sigmoid(logit(KEV原始分) + 4 × 当前动作收益标签的平均角色权重)。可选动作≥50采用；共同必需和依赖不受阈值控制。人为权重不是人格科学测量值。'));
 rules.append(make('p','ESTJ：效率/结构 +1.1，体验 −0.9，情绪支持 −0.3，边界 +0.1，少介入 +0.4。INFP：效率 −0.7，结构 −0.9，体验/情绪 +1.1，边界 +0.8，少介入 +0.4。'));
 rules.append(make('p','未发现相关能力时不因评分高而调用；一个可选能力编译失败不会让其他合法能力全部作废。'));
 const anchor=make('a','查看本轮完整中立理解、原始评分和编排记录');anchor.href='/api/persona-demo/'+new URLSearchParams(location.search).get('job')+'/audit';anchor.target='_blank';rules.append(anchor);box.append(rules);return box;
}
function renderResources(result){
 const variants=result.variants||{},box=make('section',undefined,'resource-choice-panel');let count=0;
 for(const [kind,title] of [['place','具体地点怎么选'],['media','具体内容怎么选']]){
  const left=variants.ESTJ?.resource_choices?.[kind],right=variants.INFP?.resource_choices?.[kind];if(!left||!right)continue;count++;
  box.append(make('h3',title),make('p','同一批虚拟候选，先保留KEV原始份额，再按公开角色属性权重强化。最终取综合份额最高者；这里是互斥选择，不用50分阈值。','trace-legend'));
  for(const [pid,decision] of [['ESTJ',left],['INFP',right]])box.append(make('p',pid+'：'+(decision.used?decision.reason:'本轮未采用此类动作；下方只展示内容预评估。')));
  const table=make('table'),head=make('tr');for(const label of ['共同模拟候选','ESTJ · 原始 → 综合份额','INFP · 原始 → 综合份额'])head.append(make('th',label));table.append(head);
  for(const row of left.rows){const peer=right.rows.find(r=>r.id===row.id);const tr=make('tr',undefined,row.selected!==peer?.selected?'atomic-different':'');const name=make('th');name.append(make('strong',row.name),make('p',Object.entries(row.facts).map(([k,v])=>({minutes:'车程/时长：',cost:'费用：',environment:'环境：',description:'内容：'}[k]||'')+v).join('；'),'atomic-note'));tr.append(name);
   for(const [r,d] of [[row,left],[peer,right]]){const cell=make('td',undefined,r.selected&&d.used?'atomic-adopted':'atomic-omitted');cell.append(make('p',(r.raw_probability*100).toFixed(1)+'% → '+(r.priority_score*100).toFixed(1)+'%'),make('strong',r.selected?(d.used?'最终选择':'预选，未采用动作'):'未选'),make('p','属性依据：'+r.top_factors.join('、'),'atomic-note'));tr.append(cell);}table.append(tr);
  }box.append(table);
  const detail=make('details');detail.append(make('summary','查看强化公式与属性分来源'),make('p',left.formula),make('p','地点、音频名称及属性均由中立阶段生成，是演示资料；角色权重为人为强化规则，不能把综合份额说成KEV原始置信度。'));
  for(const pid of ['ESTJ','INFP']){const d=variants[pid].resource_choices[kind];detail.append(make('p',pid+' 权重：'+JSON.stringify(d.weights)));}box.append(detail);
 }
 return count?box:null;
}
const api={render,compared,renderContrast,renderRoleDecision,renderPipeline,renderResources};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.KevAtomic=api;
})(typeof window==='undefined'?{}:window);
