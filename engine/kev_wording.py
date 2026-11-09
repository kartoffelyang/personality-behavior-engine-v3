"""Spoken output from a selected plan; never another blocking cloud request."""
import re


def spoken_reply(text,selected):
    spoken=selected.get('spoken_reply')
    if isinstance(spoken,str) and spoken.strip():
        spoken=spoken.strip()
        nav=next((a for a in selected['actions'] if a['c']=='navigation' and a['n']=='目的地设置'),None)
        if nav:
            name=re.sub(r'^[A-Z]\d*\s+', '',str(nav['p'].get('POI','')))
            if name and name not in spoken:
                spoken=spoken.replace('这里',name,1) if '这里' in spoken else '我选'+name+'。'+spoken
        return spoken,'shared_cloud_candidate'
    actions=selected['actions'];parts=[]
    if any(w in text for w in ('好累','好烦','脑子有点乱','疲劳')):parts.append('先缓一缓。')
    def find(domain,name):return next((a for a in actions if a['c']==domain and a['n']==name),None)
    nav=find('navigation','目的地设置')
    if nav:
        name=str(nav['p'].get('POI','选定地点'));name=re.sub(r'^[A-Z]\d*\s*','',name)
        parts.append('我先把去'+name+'的导航安排好。')
    play=find('entertainment','播放控制');chosen=find('entertainment','内容选择')
    if play:
        name=str(chosen['p'].get('内容','轻松音乐')) if chosen else '轻松音乐';name=re.sub(r'^[A-Z]\d*\s*','',name)
        parts.append('给你放《'+name.replace('《','').replace('》','')+'》。')
    elif not nav and not actions:parts.append('这会儿先安静待一会儿。')
    if find('notification_mode','通知静音控制') or find('notification_mode','非紧急通知屏蔽'):parts.append('非紧急提醒先静音，重要提醒保留。')
    elif find('notification_mode','提醒延后'):parts.append('非紧急提醒稍后再看。')
    if find('notification_mode','通知摘要'):parts.append('消息的重点已经整理在这里，不用一条条翻。')
    temp=find('climate','温度调节')
    if temp:parts.append('空调调到'+str(temp['p'].get('温度',24))+'度。')
    if find('seat','座椅按摩控制'):parts.append('再开轻柔按摩，让身体放松一下。')
    if find('ambient_light','氛围灯开关'):parts.append('灯光也调柔和一些。')
    if len(parts)==1 and not actions:return selected['description'],'local_plan_wording'
    basis=selected.get('selection_basis') or {}
    if basis.get('facts'):
        evidence='；'.join(basis['facts'])
        # Preset facts come directly from the shared authored resource table.
        lead=('先看时间和费用，' if selected.get('family')=='order' and any(a['c']=='ecosystem' for a in actions) else
              '这次更看重环境和放松体验，' if selected.get('family')=='feeling' and any(a['c']=='ecosystem' for a in actions) else '')
        if lead:parts.insert(0,lead+evidence+'。')
    if not parts:
        return (''.join(a.get('d','') for a in actions[-2:]).replace('模拟','') or selected.get('description') or '这轮没有额外车控动作。'),'local_plan_wording'
    return ''.join(parts),'local_plan_wording'
