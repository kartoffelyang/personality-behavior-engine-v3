(function(root){
  'use strict';
  function diagnose(plan,session){
    const actions=session.actions||[],events=session.events||[];
    if(!actions.length){
      if(plan.status==='needs_clarification')return {code:'missing_context',title:'已理解需求，但还缺少执行信息',goal:plan.goal,questions:plan.questions||[],calls:events.length,detail:'尚未生成可执行动作，因此没有发出服务调用。补充以下信息后会重新规划，保留原始情境。'};
      return {code:'no_actions',title:'本轮没有生成工具动作',goal:plan.goal,questions:[],calls:events.length,detail:'当前没有可执行的服务请求；这不代表已处理完成。'};
    }
    const last=events.at(-1);
    if(last&&last.response.code!=='OK')return {code:'service_failed',title:'调用失败，尚未得到执行结果',detail:last.response.message,calls:events.length};
    if(session.pending){const next=session.pending,missing=next.required_fields.filter(f=>f.value===undefined||f.value===null||f.value==='').map(f=>f.name);
      return {code:missing.length?'missing_arguments':'confirmation',title:missing.length?'下一步需要补充具体参数':'下一步需要你的确认或选择',detail:missing.length?`${next.method} 缺少：${missing.join('、')}`:`待执行：${next.method}；尚未执行此动作。`,calls:events.length};}
    return {code:'complete',title:'本轮计划调用已结束',detail:'具体服务返回见下方记录。',calls:events.length};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={diagnose};else root.ExecutionStatus={diagnose};
})(typeof window==='undefined'?{}:window);
