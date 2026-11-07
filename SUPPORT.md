# Support

Start with [installation](INSTALL.md), the [minimal example](examples/minimal/README.md)
and the [author SDK](web/README.md). [Verification](docs/verification.md) records
what was actually checked and what remains unverified.

Before reporting a bug, search [existing issues](https://github.com/onehumanbeing/grok-space/issues).
Use the [bug report form](https://github.com/onehumanbeing/grok-space/issues/new?template=bug_report.md)
for a reproducible host, installer, CLI or SDK problem. Include the source tag or
commit, macOS version, architecture, expected behavior, actual behavior and the
smallest reproduction using disposable data. Distinguish the native app, browser
preview and real Bot execution; a mock preview does not establish a Bot connection.

`grok-space doctor --summary` can help local diagnosis. Do not paste its complete
output blindly: redact personal paths, installation/Bot/Space IDs and names.
Share the relevant version and safe error code instead. Never attach a live
database, credential, private chat, full action payload or unrelated screen.

For an improvement, use the [feature request form](https://github.com/onehumanbeing/grok-space/issues/new?template=feature_request.md).
Describe the user problem and a small acceptance example. Business application
code belongs in its Space project; the host stays generic. Maintainers provide
best-effort help, not a support SLA or certification of arbitrary authored code.

Report vulnerabilities through the [private advisory form](https://github.com/onehumanbeing/grok-space/security/advisories/new),
following [SECURITY.md](SECURITY.md). If private reporting is unavailable, use a
private channel already agreed with the owner; do not disclose the vulnerability
in a public issue. Conduct concerns follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

The source release builds a locally ad-hoc-signed Mac app. It is not a notarized
download. GitHub login, Apple tools and OS approvals remain user actions when
required. Do not work around a blocked installation by disabling Gatekeeper or
removing quarantine. Native Pages publication and automatic official Bot wakeup
remain planned; they are separate from registered local action workers.
