# Owner action checklist

Last reviewed: 2026-07-25  
Current machine: Windows x64  
Applies to: Doc2Audiobook desktop and any later public distribution

This is the actionable list of things that require the project owner's identity,
decision, account, or approval. It is not legal advice. Have a qualified lawyer
approve open-source, service, privacy, trademark, codec-patent, and distribution
questions before a public or commercial release.

## The most important answer

**No, do not download and attach random license files.** Most open-source
licenses are permissions supplied by dependency authors. We comply by identifying
the exact shipped version, preserving its license and copyright notices, and
meeting source or attribution obligations. I can produce that inventory and a
draft `THIRD_PARTY_NOTICES` file.

You *do* need to obtain signing identities for a normal public Windows/macOS
release, confirm that the product name and artwork are ours to use, make the
release-policy decisions below, and arrange legal review of the completed
compliance pack. Never attach private keys, certificate passwords, API keys,
recovery codes, identity documents, or Apple/Microsoft credentials to Git, an
issue, a pull request, or chat.

The current unsigned Windows build may remain in internal testing. It must not
be presented as an approved public installer.

## At-a-glance queue

Work through these in priority order. “Blocks” means the item prevents that
claim or release; it does not necessarily stop development.

| ID | Your action | When | Blocks |
|---|---|---|---|
| U-01 | Choose the owner/publisher identity and release model | now | project license, installer identity, signing enrollment, public terms |
| U-02 | Confirm provenance of the name, icon, and artwork | now | public branded installers |
| U-03 | Choose the public Edge TTS policy | before public beta | responsible public use of the current default online provider |
| U-04 | Choose an FFmpeg route and obtain legal approval | before a self-contained public build | bundling FFmpeg and public MP3/M4A/WAV exports |
| U-05 | Approve the final third-party compliance pack | before public beta | redistribution of shipped dependencies |
| U-06 | Obtain a Windows code-signing identity | before public Windows release | trusted signed `.exe`/MSI |
| U-07 | Join the Apple Developer Program and obtain Developer ID access | before public macOS release | signing, notarization, Gatekeeper distribution |
| U-08 | Supply privacy/terms inputs and approve the wording | before public beta | online text-processing disclosure and public terms |
| U-09 | Choose whether offline voices are bundled, downloaded, or excluded | before Piper/Kokoro desktop packaging | offline providers in installers |
| U-10 | Provide access to a Mac and notify me when switching | before macOS acceptance | all physical macOS gates |
| U-11 | Create paid-provider accounts only for providers you want | later/optional | real-key OpenAI/Azure/ElevenLabs verification |

## Do these first

### U-01 — Choose the publisher identity and release model

Tell me:

1. The exact public publisher name: your personal legal name or an organization.
2. Whether the app will be personal/internal only, public and free, or commercial.
3. Whether Doc2Audiobook source should be open source or remain proprietary.
4. The publisher's country, if public distribution is planned. This may stay
   private and be supplied only to signing/legal providers.

Why: the repository currently has **no top-level `LICENSE` file**, and the Tauri
identifier is the provisional `com.doc2audiobook.desktop`. A public repository
without a license does not automatically grant reuse rights. The publisher must
also match installer metadata, signatures, notices, privacy contacts, and updates.

Where: [GitHub's Choose a License](https://choosealicense.com/) explains common
open-source choices. If the code stays proprietary, ask counsel for the correct
copyright/EULA language instead of copying another product's terms.

What to give me: the four decisions. I can prepare the project license,
copyright files, package metadata, and identifier for review.

### U-02 — Confirm name and artwork ownership

Confirm where `Doc2Audiobook`, the current app icon/logo, and any bundled visual
or audio samples came from. For an external asset, retain its original URL or
purchase receipt and the terms that applied when acquired. If you created it,
say so and retain the editable source.

Why: a generated `.ico`/`.icns` or a file in Git proves possession, not the right
to redistribute it. This blocks a public branded installer, not coding or internal
tests. Do not commit receipts containing addresses, order numbers, or payment
details; Git only needs a provenance record and any public required notice.

### U-03 — Choose the Edge TTS release policy

The current default uses the third-party MIT-licensed
[`edge-tts`](https://github.com/rany2/edge-tts) package to call Microsoft Edge's
online TTS service without an API key. A package license and authorization to use
an online service are separate questions. We must not imply this is an official,
contracted, availability-guaranteed Microsoft API.

Choose one route:

- **Supported-service route:** use an account-backed service such as Azure AI
  Speech publicly; keep Edge for internal/development use.
- **Reviewed Edge route:** have counsel review the then-current Microsoft terms,
  intended usage, product wording, and privacy disclosure.
- **Offline-first route:** make cleared Piper/Kokoro voices the public default;
  leave Edge opt-in or excluded.

Why: document text is transmitted to an online TTS service to generate audio.
This blocks a responsible public claim for the current default, not internal 4A
tests. Give me the selected route and approved constraints, never credentials.

### U-04 — Decide how FFmpeg will be distributed

The current Windows artifact contains the Gyan FFmpeg 8.1.2 **full GPL build**
and is internal-only. FFmpeg's official
[`License and Legal Considerations`](https://ffmpeg.org/legal.html) page says
FFmpeg is LGPL 2.1-or-later by default, enabling GPL components makes the build
GPL, and FFmpeg is not offered under separate proprietary/commercial terms.

Choose with counsel:

1. **Bundle the present GPL build compliantly.** Preserve applicable licenses
   and notices and provide exact corresponding source/build information and all
   other required material.
2. **Replace it with a reviewed LGPL-compatible build.** We must prove that the
   exact configuration and required MP3/AAC/WAV features suit the distribution;
   the word “LGPL” alone is not approval.
3. **Do not bundle FFmpeg.** Require a separate user installation, reducing
   convenience and changing the self-contained claim.

Also ask counsel whether codec patents or royalties matter for the countries and
commercial model selected under U-01, especially AAC/M4A. An implementation's
open-source license is not automatically a patent license.

What to obtain: **not a paid FFmpeg license**. Obtain written review/approval of
the chosen route. I can assemble hashes, configure output, source archive/URL,
license texts, notices, and build instructions. This blocks any self-contained
public build using bundled FFmpeg.

### U-05 — Approve the third-party compliance pack

I can inventory the actual frozen Python packages, production JavaScript
packages, Rust crates, Tauri components, icons, and runtime files; collect their
license texts; and draft notices. You do **not** need to do this manually.

Your action:

1. nominate yourself or a qualified reviewer as approver;
2. review the inventory/notices against the exact release artifact;
3. record approval for that release version; and
4. publish the notices with the app/installer as required.

Why: downloadable, free, or package-manager availability is not redistribution
evidence. This blocks public approval, not engineering.

## Signing identities you actually obtain

### U-06 — Windows code signing

Microsoft's current [Windows code-signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
recommend Azure Artifact Signing (formerly Trusted Signing) for apps distributed
outside the Store. A suitable trusted commercial-CA certificate is another route.

Your steps:

1. Decide whether the publisher is an individual or organization; match U-01.
2. If choosing [Azure Artifact Signing](https://azure.microsoft.com/en-us/products/artifact-signing),
   create/control the Microsoft Entra/Azure tenant and billing subscription.
3. Complete identity validation and create the signing account/profile.
4. Keep owner recovery/billing control; later grant automation only a narrow
   signing role.

Do not export or send a private key, password, owner token, or identity document.
Managed signing is preferable because the private key need not enter the repo or
ordinary build files.

This blocks signed Windows executables/installers and a normal trust story. It
does not promise that SmartScreen will never warn or replace malware testing.

### U-07 — Apple Developer ID and notarization

For distribution outside the Mac App Store, enroll the U-01 publisher in the
[`Apple Developer Program`](https://developer.apple.com/programs/). Apple states
that the Account Holder can create a Developer ID certificate and that signed
software can be submitted for
[`notarization`](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

When macOS public distribution becomes active:

1. Enroll as the correct individual/organization and complete Apple's checks.
2. Keep the Account Holder, trusted phone numbers, recovery methods, and
   agreements under your control.
3. On the Mac, create/authorize the Developer ID Application identity; keep its
   private key in Keychain or approved CI secret storage.
4. Create notarization credentials only when needed and keep them in Keychain/CI.

Never send a `.p12`, private key, Apple password, recovery code, or API-key file
through chat or Git. I can configure the workflow while secrets remain local.
This blocks signing, notarization, stapling, and normal Gatekeeper distribution,
but not unsigned building/testing on your Mac.

## Release documents and data handling

### U-08 — Privacy, terms, and support inputs

Provide or approve:

- public publisher/contact name and support/privacy email;
- intended user countries and whether children, schools, or workplaces are a
  target audience;
- whether the app is free, paid, subscription, or donation-supported;
- a disclosure that online providers receive document text for speech generation
  while local providers process locally;
- retention, deletion, and support promises we can actually keep; and
- whether crash/log collection will exist (it must never silently upload document
  text or secrets).

I can draft an in-app disclosure, privacy policy, EULA/terms shell, and support
policy. A qualified reviewer approves them for the chosen regions/channel. Do
not claim “fully offline” while a cloud provider is selected. This blocks a
responsible public beta/store submission, not local development.

### U-09 — Offline voice/model packaging decision

Piper and Kokoro work from source but are excluded from the 4A installer. Choose:

- bundle a small approved seed voice/model;
- download selected files on first use from immutable, checksum-verified sources;
  or
- keep offline providers out of desktop releases for now.

Review each runtime, model, and voice separately; a collection-level label does
not prove identical rights for every file. Current sources include
[`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices) and the
[`Kokoro-82M`](https://huggingface.co/hexgrad/Kokoro-82M) model card. I can build
the per-file provenance pack; you approve the policy and legal review. This
blocks advertising these as included offline voices, not Edge-only 4A.

## Machine access and optional accounts

### U-10 — macOS access

No license purchase is needed merely for macOS engineering. When switching to
the Mac, notify me first so Windows evidence remains intact. Then run:

```bash
python bootstrap.py --desktop
```

If bootstrap reports Xcode absent, install the current Xcode from the Mac App
Store, open it once, and personally accept Apple's prompts. Signing credentials
are only needed for U-07. Follow [`MACHINE_SWITCH.md`](MACHINE_SWITCH.md).

This blocks physical macOS build, provider, path, process-cleanup, and installer
acceptance. A Windows cross-build is not a substitute.

### U-11 — Paid providers (optional; not a 4A blocker)

Create accounts only for providers you decide to ship. OpenAI is implemented but
unverified with a real key; Azure and ElevenLabs are planned. Use each provider's
official billing console, read current service/data terms, set a small budget and
alerts, and create a restricted project key.

Keep keys in the OS keychain or local environment. Never paste them into chat,
screenshots, committed `.env`, frontend code, installers, tests, or docs. Tell me
only that a key is configured and its environment-variable name. Polished key
entry remains blocked until OS keychain storage exists.

## What I can continue without anything from you

- Finish remaining safe Windows 4A functional tests.
- Improve unit, integration, security, and packaging tests.
- Generate dependency inventories and a draft third-party-notices pack.
- Stage an FFmpeg compliance candidate without publishing it.
- Build Piper/Kokoro per-file provenance while excluding models from installers.
- Draft privacy, EULA, attribution, and release templates marked for approval.
- Continue Phase 5 work without calling it publicly release-ready.

Actions that change installed-program state or require account ownership will be
called out first. Installer install/repair/uninstall testing may require your
approval on this Windows machine.

## What to send me next

Start with one reply:

```text
U-01 publisher name:
U-01 release model: internal / public free / commercial
U-01 source policy: open source (preferred license) / proprietary / undecided
U-01 publisher country (may remain private):
U-02 name and icon provenance: created by me / source details / investigate
U-03 Edge policy: supported Azure / reviewed Edge / offline-first / undecided
U-09 offline model policy: bundle / first-run download / exclude / undecided
```

“Undecided” is acceptable; we preserve the internal-only boundary while work
continues. Handle U-06/U-07 later. “Enrollment complete and signing identity is
available locally” is sufficient proof—never send secrets.

## What belongs in Git vs. private storage

| Safe to commit | Keep private |
|---|---|
| public license texts, notices, versions, hashes, source URLs | signing keys, `.p12`, passwords, recovery codes |
| FFmpeg configure output and source/build instructions | Apple/Microsoft credentials and identity documents |
| public model cards, license IDs, required attribution | API keys, billing details, private contracts |
| signature thumbprints, public certificate identity, notarization result | private legal advice unless approved for publication |
| approved privacy policy, EULA, support policy, release notes | receipts with personal/payment information |

## Definition of unblocked

A public release is unblocked only when functional tests pass **and** relevant
owner actions have recorded evidence. A certificate does not fix license
compliance; notices do not replace signing; and signing/notarization does not
approve provider terms, privacy wording, or model redistribution.
