"""Shared resource choice: retain Kev probabilities and publish authored utility."""
import math

WEIGHTS={
 'ESTJ':dict(efficiency=.35,value=.2,certainty=.2,structure=.2,atmosphere=.02,empathy=.01,novelty=.01,freedom=.01),
 'INFP':dict(efficiency=.01,value=.01,certainty=.01,structure=.02,atmosphere=.15,empathy=.15,novelty=.4,freedom=.25),
}
LABELS=dict(efficiency='用时效率',value='费用',certainty='确定性',structure='结构清晰',atmosphere='环境体验',empathy='情绪匹配',novelty='新颖性',freedom='自主空间')
STRENGTH=6


def choose(resources,probabilities,pid):
    def number(v):return float(v) if type(v) in (int,float) and math.isfinite(v) else None
    times=[number(r.get('minutes')) for r in resources.values()];times=[v for v in times if v is not None]
    costs=[number(r.get('cost')) for r in resources.values()];costs=[v for v in costs if v is not None]
    def normalized(value,values):
        if value is None or not values or max(values)==min(values):return None
        return (max(values)-value)/(max(values)-min(values))
    rows=[]
    for ident,r in resources.items():
        facets={k:max(0,min(1,float(v))) for k,v in (r.get('facets') if isinstance(r.get('facets'),dict) else {}).items() if k in LABELS and number(v) is not None}
        for key,field,values in [('efficiency','minutes',times),('value','cost',costs)]:
            v=normalized(number(r.get(field)),values)
            if v is not None:facets[key]=v
        available=sum(WEIGHTS[pid][k] for k in facets)
        utility=sum(WEIGHTS[pid][k]*v for k,v in facets.items())/available if available else .5
        # Absent attributes cannot manufacture a contrast.
        score=math.log(max(1e-8,probabilities[ident]))+(STRENGTH*utility if available else 0)
        contributions=sorted(facets,key=lambda k:WEIGHTS[pid][k]*facets[k],reverse=True)
        rows.append({'id':ident,'name':r['name'],'raw_probability':probabilities[ident],'fit_score':round(utility,4),
                     'facets':facets,'decision_log_score':score,'top_factors':[LABELS[k] for k in contributions[:3]],
                     'facts':{k:r[k] for k in ('minutes','cost','environment','description') if k in r}})
    highest=max(r['decision_log_score'] for r in rows)
    total=sum(math.exp(r['decision_log_score']-highest) for r in rows)
    for r in rows:r['priority_score']=round(math.exp(r['decision_log_score']-highest)/total,4)
    selected=max(rows,key=lambda r:r['decision_log_score'])
    for r in rows:r['selected']=r['id']==selected['id']
    return selected['id'],{'rows':rows,'selected':selected['id'],'raw_choice':max(probabilities,key=probabilities.get),
        'weights':WEIGHTS[pid],'strength':STRENGTH,'source':'kev_raw_plus_authored_resource_utility',
        'formula':'softmax(log(KEV原始份额)+6×角色属性适配度)；综合分不是模型概率',
        'reason':'本轮优先比较'+ '、'.join(selected['top_factors'])+'，选'+selected['name']+'。'}
