from engine.kev_resource_choice import choose


def test_balanced_shared_candidates_produce_different_choices_without_index_assignment():
    pool={'A':{'name':'近处便利去处','minutes':10,'cost':0,'environment':'普通、路线明确',
               'facets':dict(certainty=1,structure=1,atmosphere=.1,empathy=.1,novelty=.1,freedom=.2)},
          'B':{'name':'庭院慢游','minutes':25,'cost':30,'environment':'安静、有庭院、可随意停留',
               'facets':dict(certainty=.5,structure=.2,atmosphere=1,empathy=.9,novelty=.9,freedom=1)}}
    probs={'A':.52,'B':.48}
    e,es=choose(pool,probs,'ESTJ');i,is_=choose(pool,probs,'INFP')
    assert e=='A' and i=='B'
    assert es['rows'][0]['raw_probability']==is_['rows'][0]['raw_probability']==.52
    assert sum(r['priority_score'] for r in es['rows'])==1
    assert choose(dict(reversed(list(pool.items()))),probs,'ESTJ')[0]=='A'
    assert choose(dict(reversed(list(pool.items()))),probs,'INFP')[0]=='B'


def test_no_attributes_means_no_fabricated_resource_separation():
    resources={'A':{'name':'同等候选A'},'B':{'name':'同等候选B'}}
    assert choose(resources,{'A':.6,'B':.4},'ESTJ')[0]=='A'
    assert choose(resources,{'A':.6,'B':.4},'INFP')[0]=='A'
