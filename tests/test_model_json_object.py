from engine.llm_client import extract_json_object


def test_accept_complete_object_with_redundant_gateway_braces():
    assert extract_json_object('{"situation":{},"opportunities":[]}}')=={'situation':{},'opportunities':[]}
    assert extract_json_object('```json\n{"x":{"text":"}"}}}\n```')=={'x':{'text':'}'}}


def test_does_not_invent_missing_fields_or_choose_between_multiple_objects():
    assert extract_json_object('{"x":[1,2') is None
    assert extract_json_object('{"x":1}{"y":2}') is None
