# Installation and updates

Start with [INSTALL.md](../INSTALL.md). It is the agent-readable installation
contract and contains the support matrix and the sentence to give Grok.

From a local source checkout:

```bash
./scripts/install.sh --check
./scripts/install.sh --no-launch
```

Check the first command's JSON before installing. `installed` and
`installed_running_reusable` mean the existing app is reusable; skip installation
unless an update or repair was requested. The installed bundle's signature and
CLI are checked using a temporary data directory, without consulting the user's
saved database, compiler, source checkout or GitHub access. If the CLI symlink is
missing, the result supplies the usable bundled CLI path. This is package health,
not proof of a live page or Bot connection. `not_installed` means prerequisites
passed but installation has not happened. A check writes only disposable
temporary diagnostics; it never installs or launches an app.

To fetch a public tag, branch or commit without GitHub authentication:

```bash
./scripts/install.sh --repo onehumanbeing/grok-spaces --ref main
```

The installer tries HTTPS Git with a minimal environment, credential helpers and
prompts disabled. Only a failed public fetch falls back to existing authorized
GitHub CLI access for a private repository. It never starts a login. Use a
released tag such as `v0.1.0` instead of `main` when a versioned install is wanted.
Verify that the selected ref exists. Missing
Apple tools or applicable private access is reported for the user to complete;
the installer does not install system components.

When a Bot cannot open a document link, use the raw-file fallback in
[INSTALL.md](../INSTALL.md), on the selected local Mac in the user's front Bot
chat. Private forks can use this Mac's existing authorized GitHub CLI. Keep the
requested owner/repository/ref consistent between the instruction and checkout.
Report the actual installed version, including when reusing an older healthy app.

The build uses an independent source copy containing only `bridge`, `web`,
`native` and `scripts`. It excludes live databases, authored business projects,
runtime artifacts and Git credentials. The existing build script signs the
local app. Signature verification and an isolated packaged-CLI doctor must pass
before installation. The user's database is not used for these checks.

Updates exchange the staged and installed app atomically on the same macOS
filesystem, then retain the old bundle under the Applications backup directory.
The CLI link is replaced atomically. A failed post-install verification restores
the old bundle. If the target is running, installation stops without quitting it.
If a CLI path belongs to another installation or file, it is left untouched.
The running-app check compares macOS kernel executable paths rather than process
display text, so long paths and forged command-line names do not determine app
identity. It does not stop any process. The final install receipt records whether
launch was requested, accepted by macOS, blocked, or skipped for an isolated
prefix; acceptance alone is not a loaded-page check.

Keep the backup until satisfied. For manual recovery, quit Grok Spaces and use
the retained previous local bundle with the installer:

```bash
./scripts/install.sh --app "/absolute/path/to/backup/Grok Spaces.app" --no-launch
```

`--app` accepts a previously built, signature-verifiable local bundle. It does
not download or bless an unsigned release. The current app is backed up again
when recovering, and saved data remains separate.

For isolated verification:

```bash
./scripts/install.sh --app "out/Grok Spaces.app" --prefix /tmp/grok-space-install-test --check
./scripts/install.sh --app "out/Grok Spaces.app" --prefix /tmp/grok-space-install-test --no-launch
```

A custom prefix never auto-launches or selects the user's database. Its app,
CLI link, receipt and backups all live below that prefix. Installer tests use
this path and owned temporary fixture processes; they do not manipulate a user's
running app. Installing through an isolated prefix is not proof of a real Bot
connection or native Pages publication.
