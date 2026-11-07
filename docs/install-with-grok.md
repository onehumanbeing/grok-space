# Install with your existing Grok Bot

Send this in the **front chat of the Bot you want to use**, with official local
execution pointed at this Mac:

> Install Grok Spaces on this Mac: https://github.com/onehumanbeing/grok-spaces/blob/main/INSTALL.md

The canonical source is `main`; a released tag such as `v0.1.0` can replace it in
both the document URL and checkout. Public source installation needs Git and
Apple tools, not a GitHub login. If the Bot's web reader cannot open a document
or the requested fork is private, local execution can read it through this Mac's
already authorized GitHub CLI. This example selects `main`:

```bash
install_docs=$(mktemp -d /tmp/grok-space-instructions.XXXXXX)
gh api -H 'Accept: application/vnd.github.raw+json' \
  'repos/onehumanbeing/grok-spaces/contents/INSTALL.md?ref=main' \
  > "$install_docs/INSTALL.md"
```

Read that file only if the command succeeded. Never paste a GitHub token into the
chat or copy credentials into a cloud computer. If local execution or repository
access is missing, the Bot must report the exact blocker and let you finish the
required device selection, login or system approval yourself.

A background agent or another Bot is not a substitute for this front chat's
selected Mac. The Bot must confirm the device before commands, and must not
switch devices, change approval policies or claim that a cloud-side install
installed anything on your Mac.

A healthy existing installation should be reused. For an explicit update, quit
the companion yourself when asked, then let the installer stage and verify the
new app. Saved Space data remains separate. Once installed, normal opening and
use do not require terminal commands.

Use the same selected ref throughout. Report the version actually returned by
the installed CLI; reusing an older healthy app is not an upgrade to the version
mentioned in an instruction. Installation does not change repository visibility.
Native Pages publication and webhook pairing remain separate capabilities and
must be reported with their actual connection status.
