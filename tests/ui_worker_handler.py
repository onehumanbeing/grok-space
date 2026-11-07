#!/usr/bin/env python3
"""Internal deterministic worker fixture; writes only to its owned project."""
import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
project = Path(payload["refs"]["project_dir"])
count_file = project / "ui-executions.jsonl"
with count_file.open("a", encoding="utf-8") as output:
    output.write(json.dumps({"event_id": payload["event"]["id"]}) + "\n")
executions = len(count_file.read_text(encoding="utf-8").splitlines())
values = payload["event"]["payload"]["values"]
stats = {"total": sum(values), "count": len(values), "executions": executions}
result = dict(stats, online_only=payload["snapshot"]["state"].get("online_only"))
print(json.dumps({"result": result, "patch": {"worker_stats": stats}}))
