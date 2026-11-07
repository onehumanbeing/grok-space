# Contributing

This project uses the [MIT license](LICENSE). Use focused branches and reviewable
pull requests. Follow the same license for contributed source and identify any
third-party material and its license.

Follow the [community conduct](CODE_OF_CONDUCT.md). Questions and ordinary bug
reports follow [Support](SUPPORT.md); vulnerabilities use the private route in
[Security](SECURITY.md).

Keep the foundation generic: business applications belong in their Space project
directory. Prefer standard macOS/Python APIs; justify new dependencies. Preserve
the five-family CLI and documented SDK.

Use isolated data folders and ports. Never copy live databases, browser profiles,
credentials, proprietary renderer bundles or private chats into development.
Record the source Git state when deriving a worktree.

Pages can invoke registered actions without a physical click. Review both the
page and handler when changing action authority. Preserve scoped frame identity,
explicit revisions, durable claims and safe errors. A conflict or timeout must
never automatically repeat an external side effect.

Use the existing CLI and SDK for applications. Prefer standard-library handlers;
keep optional dependencies in an explicitly registered project environment.
Do not add business-specific host routes, automatic package installation, account
login, permission escalation or security-warning bypasses.

Run the README's integration suite, syntax checks and macOS build. Changes to
concurrency, isolation or persistence need meaningful integration coverage.
UI, offline tests and real-Bot checks are different evidence; state which ran.

Use relative repository paths in public examples. Keep personal workspace paths,
private IDs and experimental media in a private archive, outside public Git.
Historical planning notes are snapshots, not the current product contract.
