# Install Grok Space on this Mac

Give your existing Grok Bot this sentence:

> Install Grok Space on this Mac: https://github.com/onehumanbeing/grok-space/blob/main/INSTALL.md

The canonical source is `onehumanbeing/grok-space`, branch `main`. For a versioned
installation, use a released tag such as `v0.1.0` in both the document link and
checkout. Verify that the requested ref exists; a branch name is not a signed
release. Report the version actually returned by the installed CLI, rather than
assuming that a reused installation matches the document's version.

Official prerequisite help: [GitHub CLI authentication](https://cli.github.com/manual/gh_auth_login)
and [Apple Command Line Tools](https://developer.apple.com/documentation/xcode/installing-the-command-line-tools/).

## Instructions for Grok

Use official **local execution on the user's Mac**, not your cloud computer.
Confirm the selected device before running commands. If local execution is not
available, explain the missing setting and stop. Do not change it yourself.
Keep the work in the user's current front Bot chat. Do not silently hand it to a
background agent, another Bot or another selected device. Never change execution
approval policies or infer that cloud-side files are installed on the Mac.

1. Check that this is macOS and report the detected OS/architecture. Use the
   support matrix below. Never imply that a deployment target is a tested OS.
2. Check for an existing installation before cloning or checking build tools. If
   `~/Applications/Grok Spaces.app/Contents/Resources/grok-space` exists, run its
   `doctor` with an explicit fresh temporary `--data-dir`. This identifies the
   packaged CLI without reading the user's saved Spaces. If healthy, reuse it
   unless the user asked to update or repair it. Report the actual installed
   version; do not claim it matches a remote ref without checking provenance.
3. For a new install or explicit update, use public HTTPS Git first. Public users
   need Git and Apple Xcode Command Line Tools (Swift 6 and Python 3.9+), not a
   GitHub login. Only private-repository fallback uses this Mac's existing
   authorized `gh`; check `gh auth status` without printing credentials. If the
   applicable Git/repository access or Apple prerequisites are missing, stop with
   the specific official
   prerequisite the user must finish. Do not start OAuth, OS installation, accept
   a license, use sudo, or copy GitHub credentials to another computer.
4. Fetch the requested tag/ref into a fresh temporary checkout. Use `main` or the
   user's selected released tag/ref.
   Report its resolved commit; do not call a branch a signed or reviewed stable
   release. Never overwrite the user's
   development checkout or reuse a running app/data directory as build input.
5. Run the installer's check from that checkout:

   ```bash
   ./scripts/install.sh --check --source "$PWD"
   ```

   `installed` or `installed_running_reusable` means a signature-verifiable app
   and its packaged CLI passed an isolated temporary doctor check. Reuse that
   installation; only its Python runtime is needed, with no Swift compiler or
   GitHub check. This does not prove a running page or Bot connection.
   `not_installed` means prerequisites
   passed and installation is still needed. The check creates only temporary
   diagnostic files, never reads the user's database, and never launches an app.
   For a new install or a user-requested update, run:

   ```bash
   ./scripts/install.sh --source "$PWD"
   ```

   The installer stages a local build, verifies its signature and packaged CLI
   using a temporary database, then installs under the user's home directory.
   If the installed app is running, ask the user to quit it and retry. Never kill
   it or replace it while running.
6. Verify the installed CLI with a fresh temporary `--data-dir` and `doctor`.
   Report the installed path,
   resolved source commit, backup path (for an update), and any incomplete step.
   A macOS launch acknowledgment is not proof that the page loaded or that a Bot
   connection works. Keep existing Application Support data.

If you already have a source checkout containing the installer, it can fetch a
selected remote ref itself. It first tries credential-free HTTPS Git with prompts
disabled; only a failed public fetch tries existing authorized `gh` access:

```bash
./scripts/install.sh --repo onehumanbeing/grok-space --ref main --check
./scripts/install.sh --repo onehumanbeing/grok-space --ref main
```

For a fresh public checkout after publication:

```bash
install_checkout=$(mktemp -d /tmp/grok-space-source.XXXXXX)
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
  GIT_ASKPASS=/usr/bin/false git clone --depth 1 --branch main \
  https://github.com/onehumanbeing/grok-space.git "$install_checkout/source"
```

Only after clone succeeds, follow step 5 in that checkout. No GitHub account is
required for this public path. Use a tag instead of `main` when one was selected.

For a private fork or a document the browser cannot read, this Mac's already
authorized GitHub CLI can fetch the raw file. Use the same requested ref here
and in the checkout; this example selects `main`:

```bash
install_docs=$(mktemp -d /tmp/grok-space-instructions.XXXXXX)
gh api -H 'Accept: application/vnd.github.raw+json' \
  'repos/onehumanbeing/grok-space/contents/INSTALL.md?ref=main' \
  > "$install_docs/INSTALL.md"
```

Read it only after a successful fetch. Do not substitute a logged-in browser,
copy a token, or start a new login. Use the requested tag/ref in both the document
URL and checkout. Use `blob/main/INSTALL.md` or a released
`blob/vMAJOR.MINOR.PATCH/INSTALL.md` and fetch that same ref. For a private fork,
replace the owner/repository explicitly; do not guess a branch or switch accounts.

For a fresh private-repository checkout, use `gh repo clone` into a new temporary
directory, fetch the requested ref with `git fetch --depth 1 origin REF`, and use
`git checkout --detach FETCH_HEAD`. Then follow step 5 from that checkout. Keep
the ref as a separate command argument, never as interpolated shell code.

## What is installed

| Item | Default location |
| --- | --- |
| Native companion | `~/Applications/Grok Spaces.app` |
| CLI symlink | `~/.local/bin/grok-space` |
| Install receipt | `~/.local/share/grok-space/install.json` |
| Previous app on update | `~/Applications/.grok-space-backups/<stamp>/Grok Spaces.app` |
| Existing user data, retained | `~/Library/Application Support/Grok Spaces/` |

No package dependency is installed. No shell profile, login item, global OS
permission, account, webhook, or security setting is changed. The app is built
and ad-hoc signed locally; this is not a notarized, no-prerequisite download.
If macOS blocks opening it, the user reviews the warning in Privacy & Security.
Never remove quarantine attributes or disable Gatekeeper.

## Honest support matrix

| Platform | Current status |
| --- | --- |
| macOS 26.5.2, Apple Silicon arm64 | Actual development/runtime evidence |
| macOS 15.8.2, Apple Silicon arm64 | CI build, integration, browser and WebKit startup checks; live Grok use unverified |
| macOS 14+ on arm64 | Build deployment target is 14.0; other versions not runtime-tested |
| macOS 14+ on Intel x86_64 | Build path exists; Intel compilation/runtime unverified |
| macOS below 14 | Unsupported |
| Windows | Unsupported |
| Linux | Unsupported |

The installer permits an eligible untested Mac to attempt a local build and
reports that status. It does not turn an untested platform into a verified one.

## Maintenance and boundaries

Use `--check` to reuse a healthy installation. For an explicit update, rerun the
installation from the desired tag/ref after quitting the companion. An old app
backup is
retained, and a failed post-install check restores the previous app automatically.
The installer never deletes saved Space data. See [installation details](docs/installation.md)
for isolated checks and recovery.

Installation and Bot pairing are separate. A project-specific private skill and
webhook routine can provide a future one-button native Pages publication flow;
the user's initial secret/OS sign-in steps remain theirs. Never ask for a key in
chat or print one. Use the approved secret provider and secure pairing UI. Report
the currently implemented connection status rather than promising an unverified
automatic setup. See [verification and limits](docs/verification.md).

If the user also asked you to build a page, read `grok-space doctor --space SPACE_ID`
and the [author layout requirements](web/README.md#layout-and-interaction-requirements)
before designing it. Start from the actual approximately 420px iframe viewport;
do not squeeze a desktop dashboard into the companion by shrinking everything.
Verify the narrow layout and resize behavior before reporting it ready.
