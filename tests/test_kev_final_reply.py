import copy
from types import SimpleNamespace
from engine import kev_final_reply as final,kev_free


def fixture():
    variants={p:{'reply':'原事实回复','actions':[{'name':'温度调节','domain':'climate','text':'前排设为24度','parameters':{'温度':24}}],
                 'selection_basis':{'reason':'明确要求24度'}} for p in ('ESTJ','INFP')}
    audits={p:{'resources':{},'compiled_plan':{'final_explanation':'原事实回复','behavior_actions':['must_not_change']}} for p in variants}
    return variants,audits


def test_one_cloud_call_rewrites_both_after_actions_are_frozen(monkeypatch,tmp_path):
    monkeypatch.setattr(kev_free,'ROOT',tmp_path);monkeypatch.setattr(final,'get_llm',lambda *a:SimpleNamespace())
    calls=[]
    def chat(*a,**k):calls.append(1);return {'ESTJ':'前排温度设为24度。','INFP':'前排调到24度，舒服些。'},''
    monkeypatch.setattr(final,'chat_json_with_raw',chat)
    v,a=fixture();before=copy.deepcopy(v['ESTJ']['actions'])
    meta=final.rewrite_replies('24度',{},v,a,{})
    assert len(calls)==1 and meta['source']=='cloud_after_frozen_decision'
    assert v['ESTJ']['actions']==before and a['ESTJ']['compiled_plan']['behavior_actions']==['must_not_change']
    assert '24度' in v['ESTJ']['reply'] and v['ESTJ']['reply']!=v['INFP']['reply']


def test_expression_failure_keeps_decisions_and_local_facts(monkeypatch,tmp_path):
    monkeypatch.setattr(kev_free,'ROOT',tmp_path);monkeypatch.setattr(final,'get_llm',lambda *a:SimpleNamespace())
    monkeypatch.setattr(final,'chat_json_with_raw',lambda *a,**k:({'ESTJ':'设为18度','INFP':'设为18度'},''))
    v,a=fixture();before=copy.deepcopy(v)
    meta=final.rewrite_replies('24度',{},v,a,{})
    assert meta['source']=='compiled_fallback' and v==before


def test_writer_cannot_claim_unselected_route_planning():
    text='推荐城市公园，15分钟。已经规划好了路线，你可以直接出发。'
    fixed,repairs=final.constrain_reply(text,[{'name':'服务与地点搜索','parameters':{}}])
    assert fixed=='推荐城市公园，15分钟。'
    assert repairs


def test_quota_failure_is_not_misreported_as_bad_intent_or_timeout():
    e=SimpleNamespace(response_excerpt='{"error":{"code":"insufficient_user_quota"}}',status_code=403)
    notice=final.cloud_failure_notice(e,'情境理解')
    assert '额度不足' in notice and '不是输入过于复杂' in notice


def test_timeout_reports_stage_instead_of_json_parse_failure():
    e=TimeoutError()
    notice=final.cloud_timeout_notice(e,'云端情境理解',90)
    assert '90秒' in notice and '不是意图校验失败' in notice


def test_partial_stream_timeout_is_explicit_about_received_output():
    from engine.llm_router import LLMRequestError
    inner=TimeoutError();inner._llm_diagnostics={'failure_kind':'total_timeout','first_content_ms':18000}
    outer=LLMRequestError(category='timeout',message='总时限',retryable=True)
    outer.__cause__=inner
    notice=final.cloud_timeout_notice(outer,'云端情境理解',90)
    assert '模型已开始输出，但尚未完成' in notice


def test_unsupported_time_clause_is_removed_without_changing_other_facts():
    text='先安静待一会儿就行。你大约有25分钟，这段时间不放内容。等你缓过来再定。'
    fixed,repairs=final.constrain_reply(text,[],{'12','15','20'})
    assert '25' not in fixed and '先安静待一会儿就行' in fixed and repairs


def test_generated_catalog_id_and_book_quotes_do_not_change_media_identity():
    assert final.resource_mentioned('M1《雨后钢琴》','给你放《雨后钢琴》。')
    assert not final.resource_mentioned('M1《雨后钢琴》','给你放《城市慢游》。')
