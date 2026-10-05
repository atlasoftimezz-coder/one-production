# P10: document knowledge, shared learning and Resolve

Authorized by the owner on 2026-10-05 ("Start"). This extends P10; P7-P10 manual
sign-offs remain pending. No phase is declared approved by an automated result.

## Scope and order

1. Local document library: PDF, DOCX, Markdown and text, source fingerprints,
   headings and page citations, resumable/cancellable import status, bounded retrieval.
   Start with SQLite full-text search on CPU. No embedding-model download or paid API.
2. Expose the library in the UI and to both Claude and Codex studio tools, including
   team and Script Studio workers. Imported text is evidence, never permission or
   higher-priority instructions. Index only files deliberately supplied by the owner.
3. Persistent preferences and lessons shared across workers and future projects.
   Separate global, channel and project scopes. Explicit owner preferences are usable
   immediately. Agent suggestions remain candidates until approved or validated by
   recorded tool results. Keep provenance, evidence, applicable software version,
   revision history and a way to forget. Never promote a model's claim of success alone.
4. Resolve integration: inspect installed API/version, retrieve relevant documentation,
   produce a constrained edit plan, validate supported operations, and execute only
   through an explicit approval on a new disposable project. Keep ffmpeg as default.
   Rendering remains GPU/heavy-gated. No arbitrary Python supplied by a worker.
5. Automatic reuse: inject relevant active preferences and lessons at each worker call;
   preserve them across account handoffs. Current user direction overrides older memory.
   P12 retains analytics-driven, post-publication learning.

## Gates

- Library: duplicate imports are idempotent; citations lead to the correct pages;
  malformed/encrypted/empty/scanned documents report limitations; cancellation and
  restart cannot publish a partial index; changed versions remain distinguishable.
- Retrieval: known questions retrieve expected passages; no match is reported honestly;
  source and version filters work; untrusted text cannot enable tools or approvals.
- Learning: no project/channel leakage; failed results cannot create active recipes;
  evidence and source versions remain traceable; forgetting removes future retrieval;
  corrections supersede older preferences without losing history.
- Resolve: unsupported operations/versions are rejected before a change; fake adapter
  tests never open real projects. Any live app test or render requires the owner's OK.
- UI: import/search/source opening, channel settings, preference/lesson review and
  forgetting work through the protected local API. Tests use fresh data and no accounts.
- Integration: focused tests per step, then check.bat P10, manual checklist and owner
  sign-off. Commit locally after each tested step. Never push unless asked.

## Inspection findings

The supplied Resolve 21.1 reference manual has 4,351 pages and 4,611 outline entries.
Text was extractable from 17 sampled pages; contents-page leader characters need
normalization. Figure-heavy material requires the cited original page for visual context.
The installed executable reported 21.0.0.47 and its scripting reference is available.
Retrieval must not silently treat 21.1-only features as installed capabilities.

## Remaining production work

Quality gates per stage, CPU animation, ffmpeg assembly and episode packaging remain
P10 work. Voice gates retain WER <= 0.05, pitch within one semitone, -14 LUFS +/-0.5,
true peak <= -1 dBTP and complete non-overlapping subtitles. Final video must match
voiceover duration within 0.5 seconds, keep A/V drift below 40 ms and contain no black
frames. Script Studio retains its five-round/stall rules; other stages retain the
two-round reviewer cap and budgets. Resolve/Storyblocks remain optional and licensed.
