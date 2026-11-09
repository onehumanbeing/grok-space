# Changelog

## 0.2.0 — 2026-11-09

- `init`: one command sets up a Space for a Bot — starter `index.html` and
  `handler.py` in `project_dir` (never overwritten), first publish, and local
  worker registration for the given action types. The default copied setup
  prompt is now this short plumbing-only version; the full authoring prompt
  remains available through the API (`mode: "full"`).
- Local source editing: `open` exports the saved page to `project_dir/index.html`
  when absent, and `publish --from-project` publishes that file. Bot and local
  edits share one source file. **… → Open source folder** reveals it in Finder.
- Page switcher is now a full-height left drawer (also opens with an edge swipe).
  New pages can pick their Bot, and an existing page can be moved to another Bot
  (`open --rebind`, `PATCH /api/spaces/ID`); data, HTML and history are kept.
- Delete a page from **… → 删除页面…** or `grok-space delete --space ID --confirm-title TITLE`
  (`DELETE /api/spaces/ID`). Requires the exact title; refuses while a worker run is
  active; the project folder is moved to the Trash.

## 0.1.1 — 2026-11-07

- Rename the project and canonical repository to **Grok Spaces** /
  `onehumanbeing/grok-spaces`; update installation instructions and public links.
- Center the logo and place language switching and author links at the top of
  both READMEs. Chinese includes X and Xiaohongshu.
- Preserve the `grok-space` CLI, existing app/data paths, scheme and identifiers
  so installed Bots and saved Spaces keep working.

## 0.1.0 — 2026-11-07

Initial source release. See the repository's actual tag and release for the
reviewed source; a notarized app download is not part of this release.

- Persistent per-Bot local Spaces with self-contained HTML, JSON state, revision
  checks, atomic publication and saved action results.
- AppKit/WebKit companion, original monochrome app icon, independent/fullscreen
  placement and display controls.
- Five JSON CLI families: `doctor`, `open`, `read`, `publish`, and `action`.
  Compact/scoped reads avoid returning full data for every operation.
- Scoped iframe SDK and durable local workers with explicit type registration,
  duplicate protection, bounded I/O and conflict reconciliation. Registered code
  runs with the current user's OS permissions; no physical-click approval or OS
  sandbox is promised.
- Source-build installer with reuse checks, staged replacement, retained backups
  and rollback. Public HTTPS source fetch does not require GitHub authentication.
- Disposable browser preview with the real SDK and clearly mocked actions, plus
  a small standard-library page/handler example.
- MIT license, security/reporting guidance and reproducible development checks.

The host does not invoke a model for local state and action transport. Authored
handlers may make their own service/model calls. macOS 26.5.2 arm64 has actual
Grok/runtime evidence; macOS 15.8.2 arm64 passed CI and native startup. Other
eligible Mac configurations remain unverified, and
Windows/Linux are unsupported. Builds are locally ad-hoc signed, not notarized.
One-click native Grok Pages publication and official webhook pairing remain
planned; offline probes do not establish an account connection.
