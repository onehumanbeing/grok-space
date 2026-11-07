# Releasing

Foundation version: `0.1.0`. A local build, a source release and a notarized app
distribution are separate artifacts. Native Pages publication is planned and is
not a release prerequisite for the working local foundation.

1. Review the diff, [security boundary](../SECURITY.md) and current evidence in
   `docs/verification.md`. Keep model-free host work distinct from any model or
   service calls made by authored handlers.
2. Run CI and the local macOS build. Use isolated data for native smoke checks;
   verify the packaged CLI and owned-service shutdown.
3. The project uses MIT and a clean initial public source history. Keep private
   development history separate; do not import it into the public repository.
   Sanitizing current files does not remove old Git objects or commit metadata.
   Review the files and history that will actually be published.
   Enable GitHub private vulnerability reporting and verify the advisory link in
   `SECURITY.md`. Keep private archives and runtime artifacts out of the release.
4. Tag the reviewed source as `vMAJOR.MINOR.PATCH` only within the approved release
   scope. Builds use local ad-hoc signing; do not describe them as notarized or as
   a no-prerequisite download. Public app distribution needs its own signing and
   notarization decision. Never remove quarantine or disable Gatekeeper.
5. Preserve Application Support data across updates. Any future schema migration
   needs a verified backup, versioned validation and rollback guidance.

Later releases still require review; no automatic deployment or public release
is installed. Keep internal planning/research and private media in the private
archive rather than copying them into the source distribution.

## Maintainer checklist

- [ ] The tag targets the reviewed source commit. `bridge/version.py`, bundled
  Info.plist, packaged CLI and changelog identify the intended version; update
  the changelog from Unreleased to the intended release date at release time.
- [ ] The initial public snapshot contains only the approved source/community
  files. Inspect its file list and reachable history for secrets, personal paths,
  private IDs, runtime output and proprietary bundles. Keep private development
  history, internal plans/research and media in the verified private backup.
- [ ] On that exact snapshot, record CI results, the README's Python/JavaScript
  checks, native build and `codesign --verify --deep --strict` result. Identify
  the tested OS/architecture and any blocked check.
- [ ] Verify the packaged CLI against a fresh temporary data directory. Verify
  source installation and rollback with an isolated prefix, and confirm that
  `--check` reuses a healthy installation without reading saved user data.
- [ ] Exercise the minimal browser preview and distinguish its mocked actions
  from actual worker/SDK tests. Confirm revision conflicts preserve newer state
  and never automatically replay an external effect.
- [ ] Review public README/install/support links on `main`, the MIT notice, and
  the actual GitHub private advisory entry. Report unavailable settings rather
  than claiming private reporting is enabled.
- [ ] Publish source only for this release scope. Do not attach local app bundles,
  runtime databases or private evidence. Describe ad-hoc builds accurately;
  signing/notarization and binary distribution need a separate verified release.
- [ ] Retain update backups and saved data. If the release changes a schema,
  demonstrate migration and recovery on disposable copies before distribution.
