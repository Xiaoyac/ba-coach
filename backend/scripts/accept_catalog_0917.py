"""Freeze catalog+cascade after Dev only; evaluate each unchanged set once."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals/reports/rag0917"
FREEZE = OUT / "catalog_selection_freeze.json"
FILES = ["app/semantic_catalog.py", "app/retrieval_enhanced.py", "app/retrieval.py", "app/retrieval_intent.py",
         "app/graph/nodes.py", "scripts/experiment_catalog_0917.py", "scripts/compare_retrieval_0917.py",
         "evals/retrieval_cases.json", "evals/retrieval_blind_0917.json", "evals/retrieval_vector_cases.json",
         "evals/retrieval_fresh_0917_v2.json", "evals/reports/rag0917/cascade_dev.json"]


def hashes():
    return {path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in FILES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("freeze","evaluate"))
    parser.add_argument("--key",type=Path)
    parser.add_argument("--host")
    args=parser.parse_args()
    if args.action=="freeze":
        with FREEZE.open("x",encoding="utf-8") as stream:
            json.dump({"frozen_at":datetime.now(timezone.utc).isoformat(),"hashes":hashes(),
                "selected":"semantic catalog plus explicit-title lexical fast path", "model":"deepseek-v4.1-flash",
                "selection":"Dev supports baseline Hit without loss, better recall/precision/empty, fewer calls than all-semantic catalog.",
                "disclaimer":"Old Holdout and first 0917 blind are now regression sets. Only fresh_0917_v2 is first evaluated after this freeze; still synthetic and not expert-labeled."},stream,ensure_ascii=False,indent=2)
        print(str(FREEZE))
        return
    if not args.key or not args.host:
        parser.error("Explicit worker host/key required")
    frozen=json.loads(FREEZE.read_text(encoding="utf-8"))
    if frozen["hashes"]!=hashes():
        raise ValueError("Frozen sources changed")
    for name,dataset,split in (("fresh_v2","retrieval_fresh_0917_v2.json","all"),
            ("holdout","retrieval_cases.json","holdout"),("stress","retrieval_vector_cases.json","all"),
            ("regression_blind_v1","retrieval_blind_0917.json","all")):
        output=OUT/f"cascade_{name}.json"
        if output.exists():
            raise ValueError("Refusing to overwrite the first acceptance result")
        subprocess.run([sys.executable,str(ROOT/"scripts/experiment_catalog_0917.py"),"--fast-path",
            "--dataset",dataset,"--split",split,"--key",str(args.key),"--host",args.host,"--output",str(output)],
            check=True,cwd=ROOT)


if __name__=="__main__":
    main()
