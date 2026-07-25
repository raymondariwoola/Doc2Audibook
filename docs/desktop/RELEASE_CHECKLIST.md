# Desktop release checklist

Phase 4A produces an **unsigned internal development build**. This checklist
separates what must be true for that artifact from the additional requirements
for public Windows or macOS distribution.

An item is complete only with evidence. A successful compiler exit does not
prove resource licensing, installer behavior, real-provider narration, process
cleanup, signing, or notarization.

For the decisions, accounts, and approvals that only the project owner can
supply, use [OWNER_ACTIONS.md](OWNER_ACTIONS.md). It also explains what the
engineering work can finish without owner credentials.

## Artifact identity

- [x] Record the source commit.
- [x] Record whether the worktree was clean.
- [x] Record operating system, architecture, and native target triple.
- [x] Record Python, Node/npm, Rust/Cargo, Tauri, and PyInstaller versions.
- [x] Record the artifact filename, type, size, and SHA-256.
- [x] Mark the Phase 4A artifact and provenance as unsigned/internal.
- [x] Keep generated installers and staged payloads out of Git.

## Source and build gates

- [x] Run the full Python suite.
- [x] Run React/Vitest/typecheck/production build.
- [x] Run Rust formatting, Clippy with warnings denied, and unit tests.
- [x] Build the React SPA before freezing/staging.
- [x] Freeze the Python sidecar with project-pinned desktop dependencies.
- [x] Stage through `scripts/build_desktop.py`, not manual copies.
- [x] Prove the frozen sidecar from outside the source tree.
- [x] Prove the Tauri bundle starts without a terminal or repository working
      directory.
- [x] Confirm only the intended target-triple sidecar is packaged.
- [x] Confirm media/frontend are not duplicated inside both PyInstaller and
      Tauri resources.
- [x] Confirm Phase 4A contains no Piper/Kokoro model or local-provider runtime
      payload.
- [x] Generate a manifest of packaged files and checksums.

## Security and privacy gates

- [x] Bind only an owned socket on `127.0.0.1` with an ephemeral port.
- [x] Generate a fresh high-entropy bootstrap token for each launch.
- [x] Keep the token out of command lines, readiness output, logs, errors, and
      persisted files.
- [x] Keep the bootstrap value out of every URL component and pass it once
      through origin-bound, least-privilege Tauri IPC.
- [x] Accept the bootstrap token only once.
- [x] Require the strict HttpOnly session cookie for API/media routes.
- [x] Reject hostile origins on state-changing requests.
- [x] Retain loopback host restrictions.
- [x] Retain CSP, no-referrer, and nosniff headers.
- [x] Grant only the minimum Tauri capabilities required by the frontend.
- [x] Keep sidecar spawn/kill authority in Rust rather than frontend JavaScript.
- [ ] Ensure logs do not contain document text, provider secrets, session
      secrets, or unnecessary original paths.
- [x] Copy native-selected documents into managed storage before processing.
- [x] Document that Edge/OpenAI send narration text to external services.
- [x] Direct sensitive/offline use to approved source-interface local
      providers; Phase 4A desktop itself is explicitly Edge-only.

The persisted-file check applies to the corrected build. The current Windows
development profile still contains expired History rows created by the rejected
pre-fix fragment design; they cannot authenticate a stopped or future sidecar.
They are documented in [STATUS.md](STATUS.md) and should not be mistaken for a
regression in a new launch.

## Lifecycle and data gates

- [x] Use only native app data/cache/log directories for writes.
- [x] Treat installed resources as read-only.
- [x] Gracefully stop the sidecar on normal close.
- [x] Enforce a bounded shutdown timeout and process-tree kill fallback.
- [x] Verify close while idle leaves no child process.
- [ ] Verify close during Edge and FFmpeg work.
- [ ] Verify interrupted jobs reconcile clearly at next startup.
- [ ] Verify valid resumable chunks remain reusable.
- [ ] Verify completed library entries persist across restart.
- [ ] Define uninstall behavior for user library, models, cache, and logs.
- [ ] Never delete a user's durable library during an automatic update.

## FFmpeg and ffprobe redistribution

This is a hard gate for a publicly distributable self-contained installer. The
current internal artifact records its exception and is not approved for
publication.

- [ ] Select approved exact Windows and macOS binary sources.
- [x] Record the current internal Windows build's version, SHA-256, and build
      configuration.
- [ ] Record an immutable download URL/source archive for a redistribution
      candidate.
- [x] Confirm the build does not contain `--enable-nonfree`.
- [x] Classify the current Windows build as GPL.
- [ ] Satisfy the selected build's license and corresponding-source
      obligations.
- [x] Confirm the required MP3, AAC/M4A, WAV, concat, and probe functionality.
- [ ] Ship the applicable copyright/license notices.
- [ ] Ship or offer corresponding source/build information when required.
- [ ] Review installer/EULA text for compliance.
- [ ] Have the decision reviewed before public redistribution.

The Gyan “full” Windows build may be useful for local development, but its name,
presence on `PATH`, or ability to encode the required formats is not
redistribution approval. Inspect the exact build configuration and license.

## Piper and Kokoro redistribution

A model digest proves integrity but not the right to redistribute it.
Phase 4A satisfies this gate by excluding Piper/Kokoro models and
local-provider dependencies from the frozen artifact. Complete the remaining
items before adding them to a later desktop build.

- [ ] Record the exact model/voice source and immutable revision.
- [ ] Record model-card URL, author/owner, license identifier, and license text.
- [ ] Confirm redistribution and commercial-use terms for each specific file.
- [ ] Record and ship required attribution/notices.
- [ ] Verify every staged file against `voices.manifest.json`.
- [ ] Review the Piper runtime package and selected voice separately.
- [ ] Review the Kokoro runtime package, ONNX model, and voice-data file
      separately.
- [x] Keep model binaries outside Git and PyInstaller.
- [ ] Decide whether seed models ship with the installer or are
      checksum-verified first-run downloads.
- [ ] Document offline behavior accurately for the chosen design.

Do not infer that all voices under a provider or collection share one license.

## Python, Rust, JavaScript, and provider notices

- [ ] Inventory frozen Python dependencies, including transitive packages.
- [ ] Inventory Rust crates from `Cargo.lock`.
- [ ] Inventory production JavaScript dependencies from `package-lock.json`.
- [ ] Generate and review third-party notices.
- [ ] Confirm no build-only dependency is accidentally shipped as runtime code.
- [ ] Review icons, fonts, and other visual assets for redistribution.
- [ ] Review Edge TTS service terms and product wording.
- [ ] Review paid-provider SDK/terms before enabling one in a release.
- [ ] Keep `OPENAI_API_KEY` and other secrets outside the frontend and bundle.
- [ ] Resolve secure OS keychain storage before presenting paid-provider keys as
      a polished desktop feature.
- [ ] Treat Apple `say` as an operating-system capability, not a binary to
      redistribute.

## Windows x64 internal Phase 4A gate

- [x] Complete every applicable automated and frozen-artifact gate in
      [TESTING.md](TESTING.md).
- [ ] Launch the app from Explorer.
- [ ] Exercise native selection with spaces and Unicode.
- [x] Launch the direct native bundle from an unrelated working directory.
- [x] Complete a real frozen-artifact Edge render and direct native Edge
      preview.
- [x] Confirm local and paid providers are not claimed as bundled Phase 4A
      capabilities.
- [x] Complete MP3, M4A, and WAV outputs through the frozen artifact.
- [ ] Verify playback, save/download, history, restart, and resume.
- [x] Verify idle shutdown/process cleanup.
- [ ] Verify active-render shutdown/process cleanup.
- [x] Verify actionable missing-resource errors.
- [ ] Verify corrupt-resource errors, not only a missing resource.
- [x] Record artifact hash and evidence in [STATUS.md](STATUS.md).
- [x] Communicate that SmartScreen/unsigned warnings are expected.
- [x] Restrict the artifact to internal testing; do not publish it as a normal
      end-user installer.

An internal build may temporarily rely on separately installed development
resources only when that limitation is explicit. Such a build is not
self-contained.

## Public Windows release gate

Deferred beyond Phase 4A:

- [ ] Acquire and protect an Authenticode signing identity.
- [ ] Sign executables, sidecar, and installer in the correct order.
- [ ] Add a trusted timestamp.
- [ ] Verify signatures on a clean Windows account/machine.
- [ ] Test install, upgrade, repair, uninstall, and rollback.
- [ ] Test standard-user installation and paths.
- [ ] Review antivirus/SmartScreen behavior for PyInstaller one-file versus
      one-directory packaging.
- [ ] Publish checksums and signed release notes.

## Public macOS release gate

Deferred beyond Phase 4A:

- [ ] Build natively for every claimed architecture.
- [ ] Acquire and protect an Apple Developer ID identity.
- [ ] Sign nested binaries and frameworks in the correct order.
- [ ] Confirm hardened-runtime entitlements are minimal.
- [ ] Notarize the final `.app`/`.dmg` and staple the ticket.
- [ ] Verify with `codesign`, `spctl`, and Gatekeeper on a clean Mac.
- [ ] Test Finder launch, update, uninstall, and library retention.
- [ ] Physically verify Edge, media tools, and active shutdown.
- [ ] Reopen Apple `say` and offline-provider checks only when their desktop
      packaging scope is approved.
- [ ] Decide and test separate-architecture versus universal distribution.

## Updates and operations

Auto-update is not part of Phase 4A.

Before enabling it:

- [ ] choose a signed update format and trusted channel;
- [ ] define rollback and failed-update recovery;
- [ ] prove updates never overwrite the durable library or user models;
- [ ] separate stable and internal channels;
- [ ] publish a security/support policy;
- [ ] define log collection with explicit user consent; and
- [ ] rehearse key rotation and compromised-release response.

## Final approval language

Use these labels consistently:

- **Phase 4A architecture complete**: design and contracts are documented.
- **unsigned internal build**: native artifact exists but is not signed or
  approved for public distribution.
- **platform verified**: physical acceptance ran on that OS/architecture.
- **self-contained**: every runtime dependency is bundled with approved
  redistribution evidence.
- **release-ready**: all applicable security, licensing, signing, notarization,
  clean-machine, and operational gates are complete.
