import copy
from engine.kev_input import compact_state,action_impact


def test_four_blocks_preserve_unknowns_and_prohibitions_without_old_conversation():
    u={'task':{'core_goal':'听音乐','must_do':['完成播放'],'constraints':['不要调整座椅','不要读取通知']},
       'context':{'driving_state':None},'facts':[{'fact':'想听音乐','source':'utterance'}],
       'unknowns':['行驶状态未知'],'history':{'invented':'must not enter runtime'}}
    bundles=[{'id':'A','description':'播放共同演示曲目','actions':[{'c':'entertainment','n':'播放控制'}]}]
    saved=copy.deepcopy(u)
    a=compact_state('ESTJ','想听音乐，不要调座椅或读通知','共享演示曲目',bundles,u)
    b=compact_state('INFP','想听音乐，不要调座椅或读通知','共享演示曲目',bundles,u)
    assert set(a)=={'situation','constraints','role','candidates'}
    assert a['situation']==b['situation'] and a['constraints']==b['constraints'] and a['candidates']==b['candidates']
    assert a['role']['rules']!=b['role']['rules']
    assert len(a['role']['rules'])==len(b['role']['rules'])==6
    assert a['situation']['context']=={} and a['situation']['unknowns']==['行驶状态未知']
    assert a['constraints']['explicit_constraints']==u['task']['constraints']
    assert 'history' not in a and 'persona_profile' not in a and 'role_policy' not in a
    assert u==saved
    assert a['candidates']['A']['actions']==['entertainment/播放控制']


def test_action_effects_distinguish_private_reads_and_external_communication():
    assert '私人通知' in action_impact('notification_mode','通知读取')
    assert '对外通信' in action_impact('notification_mode','回复发送')
    assert '不控制车辆行驶' in action_impact('navigation','导航运行控制')
