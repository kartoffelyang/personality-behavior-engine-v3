(function(root){
  'use strict';
  const stable=x=>Array.isArray(x)?'['+x.map(stable).join(',')+']':x&&typeof x==='object'?'{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+stable(x[k])).join(',')+'}':JSON.stringify(x);
  function analyze(plan){
    const [left,right]=plan.variants||[];
    if(!left||!right)return {callDifferences:0,descriptionDifferences:0,policyDifferences:0};
    const ids=[...new Set([...left.actions,...right.actions].map(a=>a.id))];
    let callDifferences=0,descriptionDifferences=0,policyDifferences=0;
    for(const id of ids){const a=left.actions.find(a=>a.id===id),b=right.actions.find(a=>a.id===id);
      if(!a||!b){callDifferences++;continue;}
      if(stable([a.capability,a.name,a.parameters_hint,left.actions.indexOf(a)])!==stable([b.capability,b.name,b.parameters_hint,right.actions.indexOf(b)]))callDifferences++;
      if(a.details!==b.details)descriptionDifferences++;
      if(stable([a.execution_mode,a.preconditions])!==stable([b.execution_mode,b.preconditions]))policyDifferences++;
    }
    return {callDifferences,descriptionDifferences,policyDifferences};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={analyze};else root.BehaviorEvidence={analyze};
})(typeof window==='undefined'?{}:window);
