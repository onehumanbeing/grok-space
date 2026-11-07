# Security

Report vulnerabilities privately through
[GitHub's advisory form](https://github.com/onehumanbeing/grok-spaces/security/advisories/new).
Private reporting must be enabled before public release. If that form is
unavailable, use a private channel already agreed with the owner; do not put
exploit details in a public issue. Include the source version, affected boundary,
and a reproduction using disposable data. Exclude keys, runtime databases,
private chats and unrelated screenshots.

The service is a single-user local tool. Loopback and browser-origin protections
do not isolate its database from processes with the same user's permissions.
Do not expose its port to a network or tunnel.

HTML uses an opaque-origin, scripts-only sandbox and network-blocking CSP. The
SDK scopes data/actions to one selected Space and frame lifetime. HTML has no
direct filesystem or shell API, but registered actions are an indirect execution
capability: **any script in that Space can invoke an allowed action**, including
on load or a timer. There is no trusted-click check, per-action human approval
token, or automatic model review. A prompt asking a page to use buttons is not
an enforced permission boundary.

Register a worker only when you trust both its code and the Space's HTML to use
its allowed actions. A handler runs as the current macOS user; Python `-I` is
not an OS sandbox. It must validate action types and payloads and restrict its
own external effects. Time/output/concurrency limits do not restrict its file
or network permissions, impose a hard memory quota, or prove physical control
was authorized. Do not treat this host as an approval system for sensitive or
irreversible operations. Those require a separate, enforced authorization design.

Revision checks protect local state from stale writes. They cannot roll back an
external effect that happened before a timeout or conflict. A claimed event is
not executed again automatically; inspect its status and retained result before
reconciling. Never rerun a side effect merely to resolve a state conflict.

Normal service shutdown kills tracked child process groups. A forced kill or
process crash can leave children alive. See [local workers](docs/local-workers.md)
for the exact limits and recovery contract.

No Grok credentials, cookies or private chat API are required for the local host.
Keep secrets out of HTML, state, action payloads, prompts, logs and Git. Registered
handlers decide whether to call other services or models; the host does not make
those calls on their behalf or audit all handler behavior.

The optional webhook adapter is offline-tested research, not a configured Bot
connection. Any future pairing must use an approved secret provider, a dedicated
sender executable and secure user entry. HTTP acceptance must remain distinct
from completed work. Do not scrape sessions, bypass feature gates, expose the
loopback port, remove quarantine attributes or disable Gatekeeper.
