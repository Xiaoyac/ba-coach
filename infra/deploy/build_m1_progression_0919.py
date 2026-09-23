"""Build the smallest M1 progression patch from the checked-out sources.

The latency release directory is the exact source of the currently deployed
version.  Only the M1 contract files are copied from the working tree; the two
shared files are patched at known, narrow anchors so unrelated local UI,
validator, or cache work cannot enter this release.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "work/latency-release-20260919"
OUT = ROOT / "work/m1-release-20260919-final"
FILES = {
    "backend/app/m1_contract.py",
    "backend/app/clinical_fields.py",
    "backend/app/dialogue_confirmation.py",
    "backend/app/v2_workflow.py",
    "backend/app/prompts.py",
    "backend/app/router_agent.py",
    "backend/app/graph/nodes.py",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prompt_candidate() -> str:
    base = (BASE / "backend/app/prompts.py").read_text(encoding="utf-8")
    local = (ROOT / "backend/app/prompts.py").read_text(encoding="utf-8")
    pattern = re.compile(r"M1_RUNTIME_CONTRACT = \"\"\"\\\n.*?\n\"\"\"", re.S)
    match = pattern.search(local)
    assert match, "local M1 contract prompt anchor missing"
    updated, count = pattern.subn(lambda _: match.group(0), base, count=1)
    assert count == 1 and updated != base
    return updated


def graph_candidate() -> str:
    base = (BASE / "backend/app/graph/nodes.py").read_text(encoding="utf-8")
    def change(old, new):
        nonlocal base
        assert base.count(old) == 1, old
        base = base.replace(old, new, 1)
    change('from ..router_agent import decide_target_module_with_reasoning, extract_pa_card',
           'from ..router_agent import decide_target_module_with_reasoning, extract_pa_card, format_routing_reasoning')
    old = """    started = perf_counter()\n    raw, completion = await extract_module_record_detailed(\n"""
    new = """    started = perf_counter()\n    if module == \"module_1\" and evidence_turns is not None:\n        from ..m1_contract import indexed_transcript\n        transcript = indexed_transcript(evidence_turns)\n    raw, completion = await extract_module_record_detailed(\n"""
    assert base.count(old) == 1
    base = base.replace(old, new, 1)
    old = '''            "contract_path": data["m1_contract"]["path"], "missing_fields": data["m1_contract"]["missing_fields"]}'''
    new = '''            "contract_path": data["m1_contract"]["path"], "missing_fields": data["m1_contract"]["missing_fields"],\n            "validation_issues": data["m1_contract"].get("validation_issues", [])}'''
    assert base.count(old) == 1
    base = base.replace(old, new, 1)
    change('.order_by(ConversationMessage.position))).scalars().all()',
           '.order_by(ConversationMessage.position, ConversationMessage.id))).scalars().all()')
    change('''        result_line = (
            f"模块判断结果：维持 {current}，本轮不跳转。"
            if target == current
            else f"模块判断结果：{current} → {target}。"
        )
        thought = decision.reasoning_content.strip() or (
            "路由模型没有返回独立的 reasoning_content；最终模块判断仍已通过规则校验。"
        )
        routing_reasoning = f"{result_line}\\n\\n{thought}"''',
           '        routing_reasoning = format_routing_reasoning(decision, current)')
    change('''                            routing_reasoning = f"后台核验后的实际阶段：{target}。讨论及确认均在聊天中完成；没有完整、当前有效的用户同意证据时不推进。"''',
           '                            routing_reasoning = format_routing_reasoning(decision, current, target)')
    change('''                            "from_module": current, "to_module": target,
                            "completed_steps": decision.completed_steps,''',
           '''                            "from_module": current, "to_module": target,
                            "requested_target": requested_target,
                            "router_error_code": decision.error_code,
                            "json_recovery": decision.json_recovery,
                            "native_reasoning_present": bool(decision.reasoning_content.strip()),
                            "completed_steps": decision.completed_steps,''')
    return base


def main() -> None:
    assert not OUT.exists(), "Keep previous build intact; use a new output directory."
    payload_dir = OUT / "payload"
    payload_dir.mkdir(parents=True)
    files = {}
    for name in sorted(FILES):
        if name == "backend/app/prompts.py":
            text = prompt_candidate()
            data = text.encode("utf-8")
        elif name == "backend/app/graph/nodes.py":
            data = graph_candidate().encode("utf-8")
        else:
            data = (ROOT / name).read_bytes()
        target = payload_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[name] = data
    manifest = {
        "previous": "/opt/bacoach/releases/20260918T173903Z",
        "old": {name: sha((BASE / name).read_bytes()) for name in sorted(FILES)},
        "new": {name: sha(data) for name, data in sorted(files.items())},
    }
    (payload_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    archive = OUT / "patch.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in sorted(FILES):
            tar.add(payload_dir / name, arcname=name, recursive=False)
        tar.add(payload_dir / "manifest.json", arcname="manifest.json", recursive=False)
    # Test the actual upload payload over the production source, not the dirty
    # working tree. Tests/fixtures remain local and are not archive members.
    test_backend = OUT / "checkout/backend"
    test_backend.mkdir(parents=True)
    for folder in ("app", "tests", "evals", "scripts"):
        shutil.copytree(BASE / "backend" / folder, test_backend / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(BASE / "backend/pytest.ini", test_backend / "pytest.ini")
    shutil.copy2(ROOT / "backend/tests/test_m1_progression_repair_0918.py",
                 test_backend / "tests/test_m1_progression_repair_0918.py")
    for name in sorted(FILES):
        shutil.copy2(payload_dir / name, OUT / "checkout" / name)
    print(json.dumps({"archive": str(archive), "files": sorted(FILES), "manifest": manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
