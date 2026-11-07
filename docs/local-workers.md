# Local action workers

A local worker lets a registered action execute and return a result to the same
Space without copying a prompt or the host starting a model run. Grok authors the handler;
Grok Space supplies action storage, dispatch, revision checks and result delivery.
This is separate from publishing a native Grok Page. Native Pages publishing is
not connected by registering a local worker.

## Register and inspect

Ask the existing Bot to put its Python handler in that Space's project directory.
Register the file and each action type it is allowed to handle:

```sh
grok-space action worker register --space SPACE_ID --file /absolute/project/handler.py --type custom.request
grok-space action worker status --space SPACE_ID
grok-space action worker status --space SPACE_ID --event EVENT_ID
grok-space action worker status --space SPACE_ID --event EVENT_ID --output
```

`--type` may be repeated. Registration saves the association and starts no work.
There is no HTTP registration endpoint: an HTML page cannot select a local
executable or register its own handler. Only the registered action types dispatch.

Registration trusts that Python file to run as the current macOS user. This is
not an operating-system sandbox. Review the handler and its allowed actions before
registration; a saved action does not authorize unrelated external side effects.
Do not put credentials in the handler, Space state, action payload or output.
The registered file is opened afresh for each run; editing that file changes
future executions. Symlinked files and paths outside the Space project are
rejected. Registration is not a signature or a pin to one source hash.

Registration also grants the Space's scripts access to these allowed actions.
They can call the SDK on load, from a timer, or after a click. The host does not
verify a physical click, human approval token, or model approval for each call.
Trust and review the page as well as the handler. The handler must validate its
input and enforce any authorization required for sensitive external effects.

## Start with a standard-library handler

Save a small handler in the Space project, then register its explicit action
type. This example has no external effects and uses no packages or model:

```python
import json
import sys

request = json.load(sys.stdin)
if request["event"]["type"] != "custom.request":
    raise SystemExit(2)

result = {"state_keys": sorted(request["snapshot"]["state"])}
print(json.dumps({"result": result}, allow_nan=False))
```

Only protocol JSON belongs on stdout. A result may also include a `patch` object
when the operation should update local state. Do not perform external effects
before validating the request, and do not select commands, files or destinations
directly from unchecked event payloads. See the
[minimal page and handler](../examples/minimal/README.md) for an end-to-end local
example and its preview instructions.

## Optional project dependencies

Prefer the standard library. If a reviewed handler needs packages, create a
project virtual environment explicitly and install only the chosen dependencies;
the host never installs them automatically. Keep the registered handler as a
standard-library shim that calls the **absolute** virtual-environment interpreter
and a fixed project script:

```python
import json
import subprocess
import sys
from pathlib import Path

request = json.load(sys.stdin)
project = Path(request["refs"]["project_dir"])
python = project / ".venv" / "bin" / "python3"
logic = project / "worker_logic.py"

try:
    child = subprocess.run(
        [str(python), "-I", "-B", str(logic)],
        input=json.dumps(request, allow_nan=False),
        text=True,
        stdout=sys.stdout,
        stderr=sys.stderr,
        timeout=50,
        check=False,
    )
except subprocess.TimeoutExpired:
    raise SystemExit(124)
raise SystemExit(child.returncode)
```

Create `.venv` and `worker_logic.py` under the returned absolute `project_dir`.
Register the shim with the same `--file` command above; there is no alternate
interpreter registration flag. The child uses the same stdin/output JSON contract
and inherits the handler's process group. Do not use `start_new_session`, detach,
or daemonize it. Its 50-second timeout leaves margin below the host's 60-second
limit, and output streams directly into the host's combined output limit. Do not
capture unbounded child output in the shim or hide protocol errors.

## Execution contract

The host first saves an action, then dispatches it outside the state transaction.
The handler receives one JSON object on standard input containing the scoped
installation/Bot/Space/event references, saved event, and state snapshot with its
revision. It writes one JSON object on standard output containing a JSON `result`
and, optionally, a JSON Merge Patch object under `patch`. Standard output is a
protocol channel, not a log stream. The input shape is:

```json
{
  "refs": {
    "installation_id": "installation-reference",
    "bot_id": "bot-reference",
    "space_id": "space-reference",
    "event_id": "event-reference",
    "project_dir": "/absolute/space/project",
    "expected_revision": 3
  },
  "event": {
    "id": "event-reference",
    "type": "custom.request",
    "request_id": "stable-request-reference",
    "payload": {}
  },
  "snapshot": {"state": {}, "revision": 3}
}
```

The output accepts only `result` and optional `patch`; `result` is required and
may be any JSON value. A supplied `patch` must be an object. Invalid JSON,
non-finite numbers, extra output fields and a nonzero exit fail the run.

The dispatcher uses the system Python interpreter with `-I -B`, a minimal
environment and the project as its working directory. User-site packages,
`PYTHONPATH` and inherited credential variables are not passed through. No
dependency installation is performed.

The saved snapshot revision is used when committing the result and optional
patch. A concurrent edit causes a revision conflict instead of silently applying
old output to newer state. Completion and its optional patch commit together.
The host's existing event synchronization displays the committed result on the
same page. The host invokes no model; authored handlers may call their own
services or models and incur their own usage.

The local service must be running for dispatch. Its startup recovery considers
pending actions that have not been claimed. Actions without a registered handler
remain available for manual processing.

Each dispatcher process runs at most two handlers and queues at most 32 more.
Execution has a 60-second wall-clock limit and a combined 1 MiB stdout/stderr
limit. Output is consumed incrementally; stderr is discarded. The dispatcher
kills its own handler process group on timeout, output overflow or completion.
These are execution and I/O limits, not hard memory/CPU quotas or a restriction
on the files and network a trusted handler may access. Multiple dispatcher
processes have separate concurrency limits.

## Recovery and duplicate protection

Each event receives a durable execution claim before the handler starts. A second
click with the same request ID resolves to the original action. A repeated run of
an already claimed event does not launch the handler again:

```sh
grok-space action worker run --space SPACE_ID --event EVENT_ID
```

This command can dispatch an eligible unclaimed action. It is not a retry switch.
Running, crashed, stopped, timed-out and otherwise uncertain executions are not
automatically replayed, including after an app restart. At most one execution
attempt does not guarantee exactly one external side effect: a handler can change
an external system before failing to return its result.

Worker status records a durable claim, not proof that its old operating-system
process is still alive. A claim left `queued` or `running` after a crash remains
claimed. Queue-full actions stay unclaimed; startup and subsequent worker
completions drain eligible pending work in bounded batches. An explicit run is
also available when capacity returns.

Normal service shutdown stops launches and kills tracked worker process groups.
A forced kill or process crash cannot run this cleanup; it may leave child
processes alive. The durable event claim still prevents automatic duplicate
execution. This process-management boundary is another reason to run only
reviewed, trusted local handlers.

Inspect worker status and the saved action before recovery. `--output` is an
explicit local-CLI read of retained output; routine HTTP status excludes it. For
a revision conflict (`needs_reconcile`), inspect current state and the retained
result/patch, then reconcile explicitly; do not rerun the old action blindly or
substitute a newer revision. A conflict only means that local state changed; the
handler's external effect may already have happened. Never rerun that effect to
obtain a fresh revision. Create a new action only after checking the real outcome
and deciding a new execution is safe. Raw handler stderr is not a user-facing
error message.

## Model usage and current boundary

Local filtering, state reads and saves do not invoke a model. A deterministic
handler can also run without a model; whether it calls a model or another service
is a choice made by its author, not a guarantee enforced by registration.
Model usage is needed when asking Grok to author or revise code,
reason over data, or produce a native Page through its current tools. Native
`WriteCanvas` accepts the complete React TSX content; a file reference cannot be
substituted. A native Page publish therefore must not be described as zero-model
work or as already connected to this local worker path.
