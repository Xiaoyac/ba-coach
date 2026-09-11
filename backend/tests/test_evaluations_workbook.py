"""Opt-in regression on a user-supplied workbook; never modifies the source.

WORKBENCH_XLSX=<path> enables this test. Data is imported only into pytest's
isolated database. WORKBENCH_WORKBOOK_RESULT optionally saves local UI fixtures.
"""
import base64
from collections import Counter
import csv
import hashlib
from io import StringIO
import json
import os
from pathlib import Path

import pytest

from test_evaluations import ROOT, admin_headers, run_case


@pytest.mark.skipif(not os.getenv("WORKBENCH_XLSX"), reason="User workbook is opt-in; not stored in the repository")
def test_original_workbook_once(client, admin_headers):
    source = Path(os.environ["WORKBENCH_XLSX"])
    raw = source.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    response = client.post(ROOT + "/import/preview", headers=admin_headers, json={
        "filename": source.name, "content_base64": base64.b64encode(raw).decode()})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["selected_sheet"] == "评测集"
    # This regression describes this specific supplied version, not arbitrary future uploads.
    assert len(preview["cases"]) == 41
    assert [e["row"] for e in preview["errors"]] == [14,33,34,35,36,38]
    modules = dict(Counter(c["module"] for c in preview["cases"]))
    assert modules == {"module_1":15,"module_2":14,"module_3":6,"module_4":6}
    # Explicitly test the VALID subset in a disposable database. The actual UI
    # still blocks whole-file confirmation while the six reported rows remain.
    saved = client.post(ROOT + "/import", headers=admin_headers, json={"cases":preview["cases"]})
    assert saved.status_code == 201 and saved.json()["imported"] == 41
    stored = client.get(ROOT + "/cases", headers=admin_headers).json()["cases"]
    by_code = {c["case_code"]: c for c in stored}
    for case in preview["cases"]:
        assert all(by_code[case["case_code"]][k] == v for k,v in case.items())
    run = run_case(client, admin_headers, by_code["M1-001"])
    assert run["status"] == "completed" and run["model"] == "stub-1"
    assert len(run["transcript"]) == 2
    exported = client.get(ROOT + "/cases.csv", headers=admin_headers)
    assert len(list(csv.reader(StringIO(exported.content.decode("utf-8-sig"))))) == 42
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum
    result = {"source_sha256":checksum,"source_bytes":len(raw),"worksheet":"评测集",
        "valid_cases":41,"errors":preview["errors"],"modules":modules,"saved_in_isolated_db":len(stored),
        "executed_case":"M1-001","execution_status":run["status"],"model":run["model"],
        "real_llm_called":False,"source_unchanged":True,"csv_data_rows":41}
    print(json.dumps(result,ensure_ascii=False))
    destination = os.getenv("WORKBENCH_WORKBOOK_RESULT")
    if destination:
        path = Path(destination)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({"summary":result,"preview":preview,"saved_cases":stored,"run":run},ensure_ascii=False),encoding="utf-8")
