"""Pure synthetic contracts for catalog routing; never invokes a provider or retriever."""
from __future__ import annotations

import json

import pytest

from app.retrieval import index_chunk
from app.semantic_catalog import SemanticCatalog


def doc(doc_id, source_id, category, heading, content):
    return index_chunk(id=doc_id, source_id=source_id, source_name=f"source-{source_id}",
                       category=category, heading=heading, content=content)


def section_ids(catalog, module):
    return [item["id"] for item in catalog.plan_payload(module=module, query="活动计划")["sections"]]


def test_catalog_payload_has_no_case_or_label_input_and_scopes_sections():
    catalog = SemanticCatalog((
        doc(917001, 1, "BA", "活动计划", "活动计划需要具体时间。"),
        doc(917002, 2, "PA", "活动强度", "身体活动强度不同。"),
    ))
    payload = catalog.plan_payload(module="module_1", query="活动计划")
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "917001" not in serialized and "gold" not in serialized and "case" not in serialized
    assert {item["category"] for item in payload["sections"]} == {"BA"}


def test_unknown_or_out_of_scope_plan_ids_fail_closed():
    catalog = SemanticCatalog((
        doc(1, 1, "BA", "活动计划", "活动计划需要具体时间。"),
        doc(2, 2, "PA", "活动强度", "身体活动强度不同。"),
    ))
    pa_id = next(item["id"] for item in catalog.plan_payload(module="module_2", query="活动计划")["sections"]
                 if item["category"] == "PA")
    with pytest.raises(ValueError):
        catalog.candidates(module="module_1", query="活动计划", plan_raw='{"section_ids":["s999"]}')
    with pytest.raises(ValueError):
        catalog.candidates(module="module_1", query="活动计划",
                           plan_raw=json.dumps({"section_ids": [pa_id]}))


@pytest.mark.parametrize("raw", ["not json", "[]", "{}", '{"section_ids":"s1"}',
                                  '{"section_ids":["s1","s1"]}', '{"other":[]}'])
def test_invalid_plan_json_is_rejected(raw):
    catalog = SemanticCatalog((doc(1, 1, "BA", "活动计划", "活动计划需要具体时间。"),))
    with pytest.raises((ValueError, json.JSONDecodeError)):
        catalog.candidates(module="module_1", query="活动计划", plan_raw=raw)


def test_invalid_judgment_json_and_unknown_candidate_ids_are_rejected():
    documents = [doc(1, 1, "BA", "活动计划", "活动计划需要具体时间。")]
    for raw in ("not json", '{"selected_ids":["kb:999"]}', '{"selected_ids":["kb:1","kb:1"]}',
                '{"selected_ids":[]} trailing'):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            SemanticCatalog.results(raw, documents, top_k=2)


def test_judge_source_cap_and_empty_plan_never_fall_back_to_lexical_results():
    documents = [doc(1, 1, "BA", "活动计划", "活动计划需要具体时间。"),
                 doc(2, 1, "BA", "活动计划", "活动计划需要具体地点。"),
                 doc(3, 1, "BA", "活动计划", "活动计划需要具体频率。")]
    raw = json.dumps({"selected_ids": ["kb:1", "kb:2", "kb:3"]})
    assert [hit.id for hit in SemanticCatalog.results(raw, documents, top_k=3)] == ["kb:1", "kb:2"]
    catalog = SemanticCatalog(tuple(documents))
    assert catalog.candidates(module="module_1", query="活动计划", plan_raw='{"section_ids":[]}') == []


def test_round_robin_enforces_selected_section_budget():
    catalog = SemanticCatalog((
        doc(1, 1, "BA", "计划甲", "活动计划 甲一"),
        doc(2, 1, "BA", "计划甲", "活动计划 甲二"),
        doc(3, 1, "BA", "计划甲", "活动计划 甲三"),
        doc(4, 2, "BA", "计划乙", "活动计划 乙一"),
        doc(5, 2, "BA", "计划乙", "活动计划 乙二"),
    ))
    chosen = section_ids(catalog, "module_1")
    selected = catalog.candidates(module="module_1", query="活动计划",
                                  plan_raw=json.dumps({"section_ids": chosen}), limit=3)
    assert len(selected) == 3
    assert [item.source_id for item in selected] == [1, 2, 1]


def test_zero_candidate_budget_returns_no_documents():
    catalog = SemanticCatalog((doc(1, 1, "BA", "活动计划", "活动计划需要具体时间。"),))
    chosen = section_ids(catalog, "module_1")
    assert catalog.candidates(module="module_1", query="活动计划",
                              plan_raw=json.dumps({"section_ids": chosen}), limit=0) == []
