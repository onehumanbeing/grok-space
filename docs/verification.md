# Verification and limits

This is a local macOS foundation. A public source snapshot does not establish
that every Grok account, OS version or authored application works. The evidence
below was collected on 2026-11-07 on macOS 26.5.2, Apple Silicon arm64. See
[installation](../INSTALL.md) for the tested/untested platform matrix.

## Release checks

The release candidate passed 59 system-Python integration checks,
including atomic HTML/state publication, stale revision rejection, concurrent
writes, durable action claims, cross-Space isolation, result-only completion,
bounded output, process-group shutdown and pending-work recovery. Native build,
ad-hoc signature verification and packaged CLI checks passed. The public,
relocatable Playwright runner passed all 10 stages, including the disposable
preview, actual fixture worker, result subscription and strict conflict checks.
The real WebKit smoke runner loaded a 420 × 760 root with activity suppressed,
then verified its owned service and listener stopped. Optional development
dependencies passed `npm audit --audit-level=high` with no reported vulnerabilities
at the time of this check.

CI repeats Python, browser, build, signature, packaged CLI and native startup
checks on the exact source commit. Consult the [actual workflow runs](https://github.com/onehumanbeing/grok-space/actions/workflows/ci.yml)
for results; this record is not a substitute for checking a later commit.
The macOS 15.8.2 arm64 runner also passed the full suite and native startup
check. This establishes CI execution there, not a live Grok account connection.

Isolated browser checks covered delayed-response navigation, stale frame
messages, revision conflicts, completion delivery, duplicate requests, reload
persistence and unchanged synchronization without replacing the iframe. Real
WebKit startup was checked independently. Browser fixtures are generic test
content, not the real Bot-authored application.

## Actual Bot and installed app

An existing Grok Bot used official execution on the selected Mac to verify the
installation, author its own HTML and Python handler, and publish through the CLI.
The business source, data, chats and private artifact references are outside
this repository.

In the installed native companion, actual button interactions automatically
completed registered local actions and showed their results in the same page.
One statistics run took 345 ms from claim to completion; a refresh fetched 30
pages / 597 public records in approximately 10.5 seconds and preserved the saved
filter. The host did not invoke a model for those actions. These are single-run
observations, not general latency or cost promises.

Quit/reopen restored the earlier actual page, state and completed event. Window
controls were also checked across companion, independent, fullscreen and multiple
display modes. Background request suppression was checked both in isolated UI
tests and a short real OS observation. Sustained idle CPU, total WebKit helper
memory, Intel and other macOS versions remain unverified.

## Native Pages

A real Bot created, read and edited one private native Grok Page. Independent UI
checks verified interaction, reopening and pinning; the temporary control state
reset when reopened. That observation led to the companion's separate persistent
state. No Grok app patch, private chat API or feature-gate override is used.

One-click native Pages publication and official webhook pairing remain planned.
Offline MCP/webhook tests do not prove account availability or a live Bot wakeup
connection. Native Pages are not a prerequisite for using the local foundation.

## Security scope

This verification does not certify arbitrary handlers or physical-control
applications. Space scripts can invoke registered actions without a physical
click or per-action human approval. Handlers are trusted code with the current
user's OS permissions. State CAS does not undo external effects. Read
[Security](../SECURITY.md) and [local workers](local-workers.md) before registering
code with external effects.
