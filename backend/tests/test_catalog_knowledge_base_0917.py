import json
import pytest
from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
from app.providers.base import Completion
from app.retrieval import index_chunk


DOCS = (index_chunk(id=8701,source_id=905,source_name="synthetic",category="BA",
    heading="行为激活原理",content="先付诸行动，不必等待动力。RAW_EVIDENCE"),)


class Provider:
    def __init__(self, mode="ok"):
        self.mode=mode
        self.calls=[]

    async def route_detailed(self, *, system, user, max_tokens):
        payload=json.loads(user)
        self.calls.append(payload)
        if self.mode=="timeout":
            raise TimeoutError
        if "sections" in payload:
            ids=[] if self.mode=="empty" else [payload["sections"][0]["id"]]
            text=json.dumps({"section_ids":ids})
        else:
            identifier="invented" if self.mode=="invented" else payload["candidates"][0]["id"]
            text=json.dumps({"selected_ids":[identifier]})
        return Completion(text=text,model="stub",finish_reason="stop")


def kb(provider):
    knowledge=CatalogDatabaseKnowledgeBase(provider)
    async def load():
        return DOCS
    knowledge._load_index=load
    return knowledge


@pytest.mark.asyncio
async def test_catalog_runtime_returns_only_approved_current_ids():
    provider=Provider()
    knowledge=kb(provider)
    hits=await knowledge.search(module="module_1",query="没劲的时候该怎么开始？",top_k=2)
    assert [hit.id for hit in hits]==["kb:8701"]
    assert len(provider.calls)==2
    assert knowledge.ranking_mode=="catalog"
    knowledge.invalidate()
    assert knowledge._catalog is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode",["timeout","empty","invented"])
async def test_catalog_runtime_fails_closed_without_raw_fallback(mode):
    provider=Provider(mode)
    assert await kb(provider).search(module="module_1",query="没劲的时候该怎么开始？") == []
    assert len(provider.calls)==(2 if mode=="invented" else 1)


@pytest.mark.asyncio
async def test_explicit_title_and_unknown_module_do_not_call_models():
    provider=Provider()
    knowledge=kb(provider)
    assert await knowledge.search(module="module_1",query="解释行为激活原理")
    assert await knowledge.search(module="unknown",query="陌生表达") == []
    assert provider.calls==[]


@pytest.mark.parametrize("value",[0,-1,float("nan"),float("inf")])
def test_invalid_timeout(value):
    with pytest.raises(ValueError):
        CatalogDatabaseKnowledgeBase(Provider(),timeout_seconds=value)
