"""Shared authored strong-contrast candidates; no real vehicle execution."""
import copy
from .kev_atomic import action_templates, key


def scene_definition(case_id, resources):
    from .kev_bundles import POLICIES, rest_bundles
    if case_id == 'after_work':
        return rest_bundles(resources),copy.deepcopy(POLICIES),[
            ['通知管理','notification_mode',['通知静音控制','非紧急通知屏蔽','提醒延后'],['先处理外部干扰，恢复掌控感。','本轮不整理事务，通知保持原设置。']],
            ['灯光氛围','ambient_light',['氛围灯开关','颜色设置','亮度调节'],['减少额外环境调整。','用柔和环境帮助恢复体验。']],
            ['身体放松','seat',['座椅按摩控制','座椅位置调节'],['以有边界的短休息为主。','通过身体放松恢复体验。']],
            ['内容播放','entertainment',['播放控制'],['短播客或安静休息。','用具体音乐帮助情绪恢复。']]],3
    if case_id not in {'rest_stop','weekend'}:return extra_scene_definition(case_id)
    dining=case_id=='rest_stop'
    policies=copy.deepcopy(POLICIES)
    policies['ESTJ'].update(label='效率与标准优先 · 强化演示',goal='先比较时间与成本，收敛一个可执行目的地，完成安排后停止。',
        priorities=['比较成本和用时','选定明确目的地并导航','不追加音乐氛围'],stop='选店或行程、必要导航和时间核对完成即停止。')
    policies['INFP'].update(label='环境与体验优先 · 强化演示',goal='在时间可行的前提下，选择有环境吸引力的目的地，并用音乐构成完整体验。',
        priorities=['重视庭院、湖景或书店体验','播放具体的音乐','不进行额外价格比较或路线排名'],stop='选定体验目的地、完成必要导航与音乐后留白。')
    bundles=[]
    for family,indices in [('order',[0,0,0]),('feeling',[1,2,1])]:
        for number,idx in enumerate(indices,1):
            resource=resources[idx];name=resource['name'];ident=('O' if family=='order' else 'F')+str(number)
            t=action_templates(resource,case_id,'明确安排' if family=='order' else '体验优先')
            names=[('ecosystem','服务与地点搜索'),('ecosystem','服务详情获取')]
            if family=='order':names += [('ecosystem','候选方案比较'),('ecosystem','价格与优惠查询')]
            names += [('ecosystem','候选方案选择')]+[('navigation',n) for n in ('地点搜索','地点详情查看','目的地设置','路线规划')]
            if family=='order':names += [('navigation','路线比较')]
            names += [('navigation','路线选择'),('navigation','导航运行控制')]
            if not dining:names += [('navigation','预计到达时间获取'),('navigation','迟到风险判断')]
            actions=[copy.deepcopy(t[key(d,n)]) for d,n in names]
            if family=='feeling':
                music={'id':'M'+str(number),'name':['雨后钢琴','城市慢游','午后爵士'][number-1],'description':'共同虚拟音乐候选'}
                mt=action_templates(music,'after_work','体验配乐')
                actions += [copy.deepcopy(mt[key('entertainment',n)]) for n in ('内容搜索','内容选择','播放控制')]
            for a in actions:
                if a['n']=='路线规划':
                    a['p']['偏好']=(['最短用时','较少转弯','距离优先'][number-1] if family=='order' else '时间可行，保留体验余量')
                    a['d']='模拟规划'+a['p']['偏好']+'的路线。'
            description=('比较成本与路线，' if family=='order' else '优先环境体验，')+f'选择{name}：{resource["description"]}。'
            if not dining:description+='演示13:00出发，停留后直接返回，15:30前回到起点，满足16:00前返回。'
            description+=('路线偏好：'+['最短用时','较少转弯','距离优先'][number-1]+'；不追加音乐。' if family=='order' else f'播放《{music["name"]}》，不额外比较价格或路线排名。')
            bundles.append({'id':ident,'family':family,'name':name+(' · 明确安排' if family=='order' else ' · 音乐体验')+str(number),
                            'description':description,'actions':actions,
                            'selection_basis':{'criterion':'时间、费用与路线可行性' if family=='order' else '环境吸引力与出游体验',
                                'facts':[resource['description']],
                                'reason':('先把用时和费用安排清楚，选择'+name+'，让行程明确。' if family=='order' else '这次更看重'+name+'的环境与放松体验，安排相关配乐。'),
                                'tradeoff':'不追加音乐，任务安排完成即止。' if family=='order' else '接受共同可行范围内的时间或费用取舍，不再追加价格排名。'}})
    groups=[['成本比较','ecosystem',['候选方案比较','价格与优惠查询'],['比较用时和成本后收敛。','在共同可行条件下优先环境体验。']],
            ['路线比较','navigation',['路线比较'],['明确比较可行路线。','采用可行路线，不追加排名比较。']],
            ['内容播放','entertainment',['播放控制'],['任务完成即止，不增加配乐。','用音乐延续用餐或出游的体验。']]]
    return bundles,policies,groups,3


def extra_scene_definition(case_id):
    import json
    from .persona_demo import ROOT
    from .kev_bundles import POLICIES
    data=next(c for c in json.loads((ROOT/'config/kev_showcase_cases.json').read_text()) if c['id']==case_id)
    music={
        'M1':{'id':'M1','name':'雨后钢琴','description':'纯音乐12分钟'},
        'M2':{'id':'M2','name':'换个角度看今天','description':'播客15分钟'},
        'M3':{'id':'M3','name':'城市慢游','description':'音乐10分钟'}}
    bundles=[];policies=copy.deepcopy(POLICIES)
    for pid,family,side,ident in [('ESTJ','order','order','O1'),('INFP','feeling','feeling','F1')]:
        content=music[data.get('music_'+side,'M1')]
        cabin=action_templates(content,'after_work','本轮安排')
        place=action_templates(data.get('place_'+side,{'id':'P1','name':'共同目的地','description':'演示地点'}),case_id,'本轮安排')
        custom={
            key('notification_mode','通知读取'):{'c':'notification_mode','n':'通知读取','m':'auto_generate','p':{'来源':'共同演示通知集合','时间':'刚才','内容范围':'8条非紧急消息'},'d':'模拟读取8条非紧急消息。'},
            key('notification_mode','通知优先级分类'):{'c':'notification_mode','n':'通知优先级分类','m':'auto_generate','p':{'来源':'共同演示通知集合','联系人':'工作群和朋友','任务相关性':'均非紧急'},'d':'模拟分为工作讨论、推广与日常闲聊。'},
            key('notification_mode','通知摘要'):{'c':'notification_mode','n':'通知摘要','m':'auto_generate','p':{'通知集合':'本轮已读8条消息','摘要长度':'一段短摘要'},'d':'模拟摘要：3条普通工作讨论、2条推广、3条朋友闲聊，均无需立即处理。'}}
        actions=[]
        for k in data[side+'_atoms']:
            d,n=k.split('/',1)
            source=custom if k in custom else place if d in {'navigation','ecosystem'} else cabin
            a=copy.deepcopy(source[k]);a.setdefault('why','共享方案中的具体步骤');a.setdefault('basis','共同虚拟事实')
            if n=='音量调节':a['p']={'音量':20};a['d']='模拟将音量设为20。'
            if n in {'通知静音控制','非紧急通知屏蔽'}:a['p']['时长']='20分钟';a['d']='模拟'+('静音' if n=='通知静音控制' else '屏蔽')+'非紧急通知20分钟，保留紧急提醒。'
            if n=='提醒延后':a['p']['延后时间']='20分钟';a['d']='模拟将非紧急提醒延后20分钟。'
            if case_id=='stuffy_cabin' and n=='风量调节':a['p']['档位']=2 if side=='order' else 1;a['d']='模拟前排'+str(a['p']['档位'])+'档送风。'
            if n=='路线规划':a['p']['偏好']='比较可行路线' if side=='order' else '采用可行路线';a['d']='模拟规划前往'+data.get('place_'+side,{}).get('name','共同目的地')+'的路线。'
            if n=='预计到达时间获取':a['d']='演示单程15分钟，不提供真实路况。'
            actions.append(a)
        policies[pid].update(label=('任务与标准优先' if side=='order' else '体验与边界优先')+' · 强化演示',
            goal=data[side+'_goal'],priorities=[data['dimension'],data[side+'_goal'],'共同明确要求先于角色风格'],
            stop='完成本轮安排后停止，不追加候选中没有的操作。')
        bundles.append({'id':ident,'family':family,'name':data['title'].split('：')[0]+(' · 任务安排' if side=='order' else ' · 体验安排'),
                        'description':data[side+'_description'],'actions':actions,
                        'selection_basis':{'criterion':data['dimension'],
                            'facts':[data[side+'_description']],
                            'reason':data[side+'_goal'],
                            'tradeoff':'完成本轮安排后停止，不追加方案外操作。'}})
    taxonomy=[('通知梳理','notification_mode',['通知读取','通知优先级分类','通知摘要']),
              ('安静边界','notification_mode',['通知静音控制','非紧急通知屏蔽','提醒延后']),
              ('环境通风','climate',None),('身体放松','seat',None),('灯光氛围','ambient_light',None),
              ('内容播放','entertainment',['播放控制','音量调节']),('路线比较','navigation',['路线比较','预计到达时间获取']),
              ('导航启动','navigation',['导航运行控制']),('预算比较','ecosystem',['价格与优惠查询','候选方案比较']),
              ('地点选择','ecosystem',['候选方案选择'])]
    used={key(a['c'],a['n']) for b in bundles for a in b['actions']}
    groups=[[label,d,names,[data['order_goal'],data['feeling_goal']]] for label,d,names in taxonomy if any(i.startswith(d+'/') and (names is None or i.split('/',1)[1] in names) for i in used)]
    return bundles,policies,groups,2
