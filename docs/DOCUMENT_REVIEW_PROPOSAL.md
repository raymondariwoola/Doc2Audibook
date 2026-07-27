# Document Review Before Narration — product and engineering proposal

Status: proposal for owner review; no implementation is claimed  
Created: 2026-07-27  
Working name: **Review before narration**  
Suggested roadmap placement: **Phase 6 — Document refinement**

## Executive recommendation

This feature is feasible and is a natural extension of Doc2Audiobook. The safest
product shape is an **optional, offline-first review stage** between upload and
narration:

```text
Upload → Review document → Accept/reject changes → Download revised copy
       → Choose original or revised text → Narrate
```

It should not silently “fix” a document and should never overwrite the original.
Every proposed change should be explainable, reversible, and tied to an exact
location in the uploaded document.

The recommended zero-cost foundation is:

- **Harper** for initial English spelling, grammar, punctuation, and style
  suggestions. Harper is offline, open source, Rust-powered, available as a Rust
  crate/JavaScript WebAssembly library, and licensed Apache-2.0.
- **Our own deterministic structure checks** for heading hierarchy, excessively
  long paragraphs, repeated headings, empty sections, and narration-hostile
  formatting.
- A provider boundary that can later support self-hosted **LanguageTool** for
  multilingual review without changing the UI or document-application engine.
- No paid API and no cloud LLM in the first release.

Harper's project describes it as an offline, privacy-first English grammar
checker and exposes Rust and WebAssembly integrations. LanguageTool supports more
than 20 languages and can run its own local HTTP server, but introduces Java and
a larger runtime footprint. See [Harper](https://github.com/Automattic/harper),
[LanguageTool](https://github.com/languagetool-org/languagetool), and the
[LanguageTool local-server documentation](https://dev.languagetool.org/http-server.html).

## Why this improves the product

Narration exposes writing problems that are easy to miss visually: repeated
words, malformed sentences, inconsistent punctuation, unclear headings, and
awkward paragraph structure. Correcting these before synthesis can:

- improve listening clarity;
- reduce wasted renders and provider usage;
- produce a useful revised document as a second deliverable;
- make pronunciation/replacement rules easier to manage; and
- distinguish Doc2Audiobook from a basic text-to-speech wrapper.

The feature should be presented as an assistant, not an authority. Grammar tools
produce false positives, and a stylistic rewrite can change tone, facts, legal
meaning, quotations, names, formulas, or citations.

## Product principles

1. **Optional.** “Skip review and narrate” is always visible.
2. **Original preserved.** Save revisions as a new file, never in place.
3. **Offline by default.** The initial reviewer sends no document text outside
   the machine.
4. **User-controlled.** Accept individually, reject individually, accept a safe
   category, or reject all.
5. **Explainable.** Show original text, replacement, category, explanation, and
   location.
6. **Reversible.** Support undo until the reviewed snapshot is finalized.
7. **Format-aware.** Do not “correct” Markdown syntax, URLs, code, formulas,
   citations, or protected quotations as ordinary prose.
8. **Honest scope.** Grammar and spelling are not fact-checking, plagiarism
   detection, legal review, or proof that prose is correct.
9. **Narration remains stable.** The current engine and public function
   signatures remain intact; reviewed content is simply another validated input.

## Proposed user experience

### 1. Upload

After a user uploads or selects a document, show:

- **Review before narration** — recommended for editable prose;
- **Skip review** — goes directly to the current narration workflow; and
- a short privacy label: “Review runs on this device” for offline reviewers.

The preference can be remembered per device, but it should not eliminate the
choice for a newly uploaded document.

### 2. Scan and summary

Show progress by document section, then a summary such as:

```text
43 suggestions
12 spelling · 9 grammar · 14 punctuation · 8 structure/style
Estimated reading impact: 6 sentences clarified
```

If review fails, the user can still narrate the untouched original. Review must
never become a single point of failure for the existing product.

### 3. Review suggestions

Each suggestion displays:

- surrounding context with the affected text highlighted;
- proposed replacement(s);
- category and severity;
- a plain-language explanation;
- document location (page where reliable, otherwise heading/paragraph); and
- **Accept**, **Reject**, and **Ignore this rule** actions.

Filters: spelling, grammar, punctuation, clarity/style, and structure.

### 4. Bulk decisions

Offer:

- **Accept all safe corrections** — only deterministic, high-confidence
  spelling/punctuation items;
- **Review style suggestions individually**;
- **Reject all**; and
- **Undo last** / **Reset decisions**.

There should not initially be an unrestricted “accept every suggestion” button.
Structure and style suggestions are subjective and can alter meaning.

### 5. Finalize

The completion screen offers:

- **Download reviewed document**;
- **Narrate reviewed version**;
- **Narrate original version**; and
- **Return to suggestions**.

The library records which snapshot was narrated and retains a human-readable
change summary. The audio cache identity naturally changes when reviewed text
changes; no reviewed render may reuse chunks from different content.

## What “review” means in the first release

### Included

- common spelling errors and likely typos;
- repeated words;
- punctuation and capitalization;
- common grammar agreement and article problems;
- basic style warnings such as wordiness or repeated phrases;
- heading-level jumps;
- empty headings/sections;
- extremely long paragraphs or sentences;
- inconsistent simple list punctuation; and
- obvious narration hazards already understood by the speech-cleaning layer.

### Explicitly excluded

- factual verification;
- plagiarism or copyright clearance;
- legal, medical, academic, or regulatory approval;
- citation validation;
- automatic translation;
- wholesale generative rewriting;
- changing quoted text, code, URLs, formulas, or bibliographies by default;
- OCR for scanned PDFs in the first increment; and
- preserving Microsoft Word tracked changes/comments as a first-release promise.

## File-format policy

| Input | Analyze | Apply/export | First-release promise |
|---|---:|---:|---|
| `.txt` | yes | revised `.txt` | highest-fidelity starting point |
| `.md` / `.markdown` | yes | revised Markdown | protect code fences, links, front matter, and markup |
| `.docx` | yes | new `.docx` | preserve unaffected structure/styles; never overwrite original |
| text PDF | yes | revised `.docx` or `.txt` | review extracted text; do not claim layout-preserving PDF editing |
| scanned PDF | limited/no | none initially | report that OCR is required; allow original narration if extraction works poorly |

### Why Word needs special care

A Word paragraph is split into “runs” with independent formatting. Assigning a
whole paragraph's text through `python-docx` replaces its run content and removes
run-level formatting such as bold and italic. The official
[`python-docx` text API](https://python-docx.readthedocs.io/en/stable/api/text.html)
documents this behavior. Therefore, the application engine must map character
offsets to runs and apply minimal edits rather than replacing whole paragraphs.

Initial `.docx` constraints should be explicit:

- apply only suggestions with a reliable paragraph/run mapping;
- skip fields, unsupported drawings, and ambiguous cross-boundary replacements;
- preserve headers, footers, tables, styles, hyperlinks, and unaffected runs;
- save to `<original-name>.reviewed.docx`; and
- verify the saved file can be reopened and re-extracted before offering it.

### Why PDF should not be edited in place

PDF is a fixed-layout output format, not a dependable editable source model.
Extracted text may have reordered columns, missing glyphs, false line breaks, or
no text layer. The first version should analyze extracted text and export a clean
`.docx` or `.txt`, never promise a visually identical corrected PDF.

## Architecture

The review feature should be a new reusable layer, not code embedded in React,
Gradio, Tauri, or `narrate.py`:

```text
CLI / Gradio / React / Tauri
            │
            ▼
     Review service/API
            │
      ┌─────┴─────────┐
      ▼               ▼
Document adapters   Review providers
TXT/MD/DOCX/PDF     Harper + structure rules
      │               │
      └─────┬─────────┘
            ▼
 Suggestion/apply engine
            │
  original snapshot + reviewed snapshot + audit log
            │
            ▼
 Existing narration engine (unchanged input contract)
```

Suggested modules (names are provisional):

```text
review/
  models.py          canonical document, suggestion, decision, revision models
  documents.py       format adapters and protected-range extraction
  providers.py       ReviewProvider interface and provider registry
  harper.py           offline Harper adapter
  structure.py        deterministic document-structure rules
  apply.py            conflict-safe application and snapshot generation
  export.py           TXT/Markdown/DOCX reviewed-file writers
web_api/review.py     authenticated/local API adapter
```

No interface imports another interface. The review layer can be used by a future
CLI command, Gradio, React, and desktop shell. `narrate.py` continues to accept a
path; the selected reviewed snapshot is passed to it like any other document.

## Core data contracts

### Canonical document block

```json
{
  "block_id": "stable-id",
  "kind": "paragraph",
  "text": "The original visible text.",
  "location": {"section": "Introduction", "ordinal": 12},
  "source_locator": {"format": "docx", "part": "document", "paragraph": 8},
  "protected_ranges": [[0, 5]],
  "text_hash": "sha256..."
}
```

### Suggestion

```json
{
  "suggestion_id": "stable-id",
  "block_id": "stable-id",
  "start": 4,
  "end": 9,
  "original": "teh",
  "replacements": ["the"],
  "category": "spelling",
  "confidence": "high",
  "bulk_safe": true,
  "explanation": "Possible spelling mistake.",
  "rule_id": "provider-rule-id",
  "provider": "harper",
  "context_hash": "sha256..."
}
```

Offsets alone are unsafe after edits. Before applying a suggestion, verify its
block and context hashes, original substring, protected ranges, and conflicts
with already accepted changes. Apply non-overlapping replacements from the end
of a block toward the beginning so earlier offsets do not shift. A stale or
overlapping suggestion returns to review rather than guessing.

### Revision/audit record

Record:

- original file digest;
- reviewer/version/ruleset and language/dialect;
- each suggestion and user decision;
- reviewed snapshot digest;
- export result and warnings; and
- whether narration used original or reviewed content.

Do not log full document text in normal application logs. The local job/revision
store may contain document content and should follow the same access, deletion,
and desktop-auth boundaries as current uploads.

## Review-provider strategy

### Recommended first provider: Harper

Advantages:

- no subscription, API key, or per-document fee;
- operates offline;
- Apache-2.0 source license;
- fast Rust core with JavaScript/WebAssembly availability;
- small enough to fit the desktop direction; and
- strong privacy story because text remains local.

Limitations:

- English only today;
- suggestions will not match a large generative language model's deep rewriting;
- integration and packaged-license notices still require engineering and release
  review; and
- we must pin and test an exact version rather than depend on a changing global
  installation.

Recommended integration: a small versioned local review worker using Harper's
Rust core, invoked through a narrow JSON protocol by the Python review layer.
This keeps review reusable by CLI, web, and desktop rather than trapping it in
React-only WebAssembly. The worker must bind no public port and make no network
requests.

### Secondary provider: local LanguageTool

Advantages:

- broader language coverage;
- mature grammar/style rules;
- self-hostable local server; and
- LGPL-2.1-or-later core.

Costs/risks:

- Java runtime and substantially larger desktop payload;
- more memory/startup/packaging work;
- optional language/ngram resources can be large and separately licensed; and
- using a public LanguageTool endpoint would change the offline privacy promise.

LanguageTool should be an optional later provider, not the initial packaged
dependency. If implemented, default to a process owned by the app on loopback,
pin its distribution, record resource licenses, and never silently fall back to
a public server.

### Generative/local LLM provider: later experiment only

A local LLM can propose paragraph rewrites without per-request fees, but it is
not “free” operationally: model downloads are large, hardware requirements vary,
performance is inconsistent across Windows/macOS, model licenses differ, and
rewrites may invent or alter facts. It should be optional, separately downloaded,
and limited to user-requested rewrite suggestions with a visible diff. It should
never power silent bulk correction.

## API and workflow proposal

Provisional endpoints:

```text
POST   /api/reviews                 create scan from uploaded/managed document
GET    /api/reviews/{id}            summary and status
GET    /api/reviews/{id}/events     durable progress stream
GET    /api/reviews/{id}/suggestions?category=&state=
POST   /api/reviews/{id}/decisions  accept/reject/ignore a bounded batch
POST   /api/reviews/{id}/finalize   create immutable reviewed snapshot
GET    /api/reviews/{id}/download   download revised file
POST   /api/jobs                    narrate original or reviewed snapshot digest
```

Desktop endpoints use the existing authenticated sidecar session. Source web
mode retains its loopback-only controls. Requests must use generated IDs and
managed paths; clients never choose arbitrary output paths through HTTP.

Review state should be atomic and resumable like narration jobs:

```text
created → scanning → ready → applying → finalized
                  ↘ failed (original remains narratable)
```

Decisions and snapshots persist across restart. Re-running with the same file,
language, reviewer version, ruleset, and protected-range policy may reuse the
analysis cache. Any of those changes invalidates it.

## Security and privacy requirements

- Offline reviewers make no network requests; test this explicitly.
- Display a separate consent step before any future cloud reviewer receives text.
- Reuse upload size/type limits and managed-copy rules.
- Treat `.docx` as an untrusted ZIP container: bound decompressed size, parts,
  relationships, and processing time.
- Bound blocks, characters, suggestions, replacement length, and decision batch
  size.
- Sanitize rule explanations before rendering; never trust provider HTML.
- Protect URLs, email addresses, code, formulas, citations, and quoted passages
  unless the user enables those categories.
- Never overwrite the source document.
- Use atomic writes followed by reopen/re-extract validation.
- Keep review data behind the same desktop authentication and loopback-origin
  policy as document/audio APIs.
- Do not place document text in logs, telemetry, exception messages, URLs, or
  command lines.

## Testing strategy

### Unit tests

- stable block and suggestion identities;
- Unicode/code-point offsets;
- stale-context rejection;
- reverse-order application;
- overlapping/conflicting suggestions;
- protected ranges;
- accept/reject/undo and idempotency;
- cache invalidation by content/provider/rules/language;
- safe filename generation and atomic export; and
- narration selection uses the correct snapshot digest.

### Format fixtures

- TXT with CRLF/LF and UTF-8 Unicode;
- Markdown headings, links, code fences, front matter, tables, and inline code;
- DOCX paragraphs split across differently formatted runs;
- DOCX headings, tables, hyperlinks, lists, headers/footers, images, and page
  breaks;
- text PDF with multiple pages/columns; and
- scanned/poor-extraction PDF with an honest unsupported warning.

For DOCX, compare both semantic text and selected underlying XML/style invariants.
Reopen exported files with `python-docx`, re-extract them, and confirm untouched
content and media remain. Visual fixture comparison should catch unexpected
formatting loss.

### Integration/end-to-end tests

- upload → scan → individual decisions → finalize → download → narrate reviewed;
- skip review → narrate original;
- review failure → original narration remains available;
- restart during scan and after decisions;
- repeated finalize is idempotent;
- all API limits and desktop authentication remain enforced;
- reviewer process terminates on app close, including active review;
- packaged Windows and macOS workers run outside the source tree; and
- UI screenshot verification in light/dark themes and narrow/wide layouts.

## Delivery plan

### Phase 6A — Review foundation and truthful prototype

Goal: make the complete interaction real for `.txt` and Markdown without risking
Word formatting.

- canonical document/suggestion/revision models;
- `ReviewProvider` contract;
- local Harper worker proof of concept;
- deterministic structure checks;
- TXT/Markdown protected-range adapters;
- review API with persisted state;
- React summary, filters, individual accept/reject, safe bulk accept, undo;
- reviewed download and original/reviewed narration choice; and
- unit/integration/UI tests.

Acceptance gates:

- no document-text network traffic;
- original is byte-for-byte untouched;
- every applied change appears in the audit record;
- stale/overlapping suggestions cannot corrupt text;
- revised download reopens and narrates;
- skipping/failed review preserves current narration behavior; and
- CLI, Gradio, web, and desktop regression tests remain green.

### Phase 6B — DOCX preservation

- paragraph/run source mapping;
- formatting-preserving minimal edits;
- tables, hyperlinks, headings, lists, headers/footers;
- explicit unsupported-boundary warnings;
- `.reviewed.docx` export and reopen validation; and
- rich fixture and visual regression suite.

Do not claim DOCX support based only on plain-text correctness; formatting and
embedded-content preservation are release gates.

### Phase 6C — PDF review and richer structure

- text-quality/extraction-confidence reporting;
- read-only PDF review with corrected DOCX/TXT export;
- heading/section navigation;
- document-level consistency rules; and
- optional OCR planning, separately scoped and licensed.

### Phase 6D — Languages and optional advanced rewriting

- evaluate packaged/self-hosted LanguageTool;
- language/dialect selection and rule dictionaries;
- optional separately downloaded local LLM;
- paragraph rewrite diffs with explicit user action; and
- per-model hardware, license, privacy, and quality gates.

## Dependencies and cost

The proposed 6A path has **no subscription or API charge**. Costs are engineering
time, additional testing, and a modest increase in build size. Exact Harper
version, transitive licenses, worker size, and cross-platform performance must be
measured in the proof of concept before it is accepted into desktop packaging.

No new account, paid service, code-signing identity, or API key is required to
prototype and internally test this feature. Existing public-release licensing
and signing gates still apply to any installer containing it.

## Relationship to current phases

This should not interrupt or redefine Phase 4A's remaining Windows/macOS
acceptance gates. It is additive and can be developed behind a feature flag while
desktop release work continues. It belongs after audiobook polish in the roadmap
as Phase 6 because it introduces a distinct document transformation product
surface, persisted workflow, and export responsibility.

The most sensible sequence is:

1. approve this proposal and its first-release boundaries;
2. finish the remaining safe Windows 4A acceptance work;
3. build a small Harper integration spike and measure accuracy, worker size,
   startup time, and packaging behavior on Windows;
4. implement 6A for TXT/Markdown end to end;
5. proceed to DOCX only after the apply engine proves conflict-safe; and
6. validate each native integration again when moving to macOS.

## Decisions requested from the owner

Please decide:

1. **Placement:** accept Phase 6, or prioritize it before some Phase 5 items?
2. **Initial language:** is English-only acceptable for 6A?
3. **Default:** offer review prominently, or leave it off by default?
4. **Bulk behavior:** approve the recommended “safe corrections only” bulk mode?
5. **PDF behavior:** accept corrected DOCX/TXT export rather than edited PDF?
6. **DOCX fidelity:** accept a staged rollout where ambiguous edits remain
   suggestions but cannot initially be applied automatically?
7. **Advanced rewriting:** agree to defer generative rewrites until the
   deterministic offline workflow is stable?

## Final assessment

The idea is worth building. It can come alive without a paid service, but it
should be built as a revision system—not a one-click text replacement script.
The reliable first milestone is offline English review for TXT/Markdown with
fully auditable decisions and narration of either snapshot. DOCX preservation is
the next serious engineering gate; PDF correction should remain an export to an
editable format. Deep AI rewriting can wait until the product has earned trust
with deterministic, reversible corrections.
