"""Shared deterministic knowledge restrictions, independent of retrieval mode."""


def declined_recording_result(state, module, knowledge):
    bundle = state.get("knowledge_context") or {}
    task = bundle.get("task", state.get("knowledge_task", "general"))
    status = state.get("recording_status")
    if status is None:
        status = next((fact.get("values", {}).get("recording_status")
            for fact in bundle.get("facts", []) if fact.get("source") == "module_three_record"), None)
    if (module == "module_3" and status == "declined"
            and task not in {"m3_recording_purpose", "m3_recording_concern", "m3_reminder"}):
        return [], "", {"status": "completed", "reason": "not_needed", "duration_ms": 0,
            "decision": "not_needed", "selections": [], "selected_ids": [], "applications": [],
            "note": "用户已明确拒绝当前记录安排，本轮没有新的记录问题。", "guidance": "", "cautions": [],
            "raw_chunk_count": len(knowledge), "candidate_chunk_count": 0, "approved_chunk_count": 0,
            "withheld_on_error": False}
    return None


def direct_hybrid_knowledge(*, state, module, knowledge):
    withheld = declined_recording_result(state, module, knowledge)
    if withheld is not None:
        return withheld
    return list(knowledge), "", {"status": "bypassed", "reason": "hybrid_direct",
        "duration_ms": 0, "decision": None, "selections": [], "applications": [],
        "selected_ids": [str(c.id) for c in knowledge], "note": "", "guidance": "",
        "cautions": [], "raw_chunk_count": len(knowledge), "approved_chunk_count": 0,
        "provided_chunk_count": len(knowledge), "forwarded_to_reply": bool(knowledge),
        "withheld_on_error": False}
