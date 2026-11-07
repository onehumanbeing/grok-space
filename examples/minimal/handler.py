#!/usr/bin/env python3
"""Minimal standard-library action handler; no network, devices or model calls."""
import json
import sys

request = json.load(sys.stdin)
if request["event"]["type"] != "example.run":
    raise ValueError("Unsupported example action")
message = request["event"]["payload"].get("message", "")
if not isinstance(message, str) or len(message) > 200:
    raise ValueError("Expected a short message")
result = {"message": message.upper()}
print(json.dumps({"result": result, "patch": {"last_result": result}}))
