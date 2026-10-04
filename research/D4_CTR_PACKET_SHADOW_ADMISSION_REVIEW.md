# CTR packet through D4B2 shadow admission

Reviewed 2026-10-04. Initial/final HEAD: `a41fd4c371fd6858e1df0ccff06f743686ca9a43`, branch `main`. No stage, commit, push, production wiring or new source research. Exactly one reviewed real symbol-session is used: CTR / HOSE / 2022-02-17. All calendar, publication and price inputs are synthetic test data.

## Before and after

Before this change, `check_notice_packet.py` independently validated source bytes, constructed `SymbolSessionEvidence`, and called `evaluate_completed_session()`. No caller loaded this packet into `PreparedPriceBatch` and exercised `commit_price_batch_shadow()`. Existing writer tests used synthetic lifecycle evidence rather than the retained packet.

New explicit offline path:

```text
reviewed packet SHA-256 + retained source snapshots
  -> load_symbol_session_packet()
  -> existing SymbolSessionEvidence
  -> evaluate_completed_session() with synthetic calendar/publication inputs
  -> PreparedPriceBatch.completed_session_result
  -> commit_price_batch_shadow()
  -> evaluate_operational_admission()
  -> durable rejected receipt; zero price/provenance writes
```

Files added: `quantlab/symbol_session_packet_adapter.py`, `tests/test_symbol_session_packet_shadow.py`, and this report. No existing packet, source, schema, policy, completed-session evaluator or writer was modified.

The adapter requires a caller-supplied, independently reviewed packet hash. The integration tests pin the version checkpointed at the initial HEAD:

`87825cae36bea85cdf42b6daa8e477c98d6aec6b344e42ab8893f057a7e44a7e`

It verifies exact requested symbol/venue/session against both packet and mapping, NOT_TRADING-only scope, false eligibility/as-of flags, original snapshot lengths and SHA-256 (including nested source records), bundle identity and source-reference bindings. Checkpoint relocation paths must resolve within the supplied repository root. Missing or invalid evidence returns UNKNOWN with empty attributable references and a diagnostic source identity; the existing gate returns UNRESOLVED/SYMBOL_SESSION_ATTRIBUTION_INCOMPLETE. Hash integrity does not independently authenticate a new claim: never derive a new trusted pin from an unreviewed input file.

Valid evidence preserves the packet's source identity, bundle snapshot identity and original references (VSDC identity plus HOSE notice). An additional `evidence-packet:sha256:` reference binds the evaluated mapping to this exact reviewed packet version. These values and the completed-session evidence digest are checked in the persisted D4B2 receipt.

## Focused checks

`tests/test_symbol_session_packet_shadow.py`: **14 passed in 1.00s**. The snapshot-corruption test was then strengthened to preserve file length and isolate the SHA-256 check; only that affected case was rerun: **1 passed in 0.28s**. No prior staging suite, checker or clone rehearsal was rerun.

| Input | Completed-session / D4B2 behavior |
|---|---|
| Reviewed CTR/HOSE/2022-02-17 packet, intact snapshots | NOT_TRADING; REJECTED/SYMBOL_NOT_TRADING; OPERATIONAL_REJECTED with SYMBOL_SESSION_NOT_TRADING |
| Requested different symbol, venue, 18 February, first-day 23 February, or 24 February | UNKNOWN; no interval or future-session extrapolation; UNRESOLVED and OPERATIONAL_REJECTED |
| Packet missing or changed without matching reviewed pin | UNKNOWN; UNRESOLVED and OPERATIONAL_REJECTED |
| Original HOSE notice snapshot missing or corrupted at the same byte length | UNKNOWN; snapshot is not valid evidence; UNRESOLVED and OPERATIONAL_REJECTED |
| Negative fixture with pinned but internally mismatched symbol/venue/session mapping | UNKNOWN; UNRESOLVED and OPERATIONAL_REJECTED |
| Negative fixture attempting TRADING_CONFIRMED mapping | UNKNOWN; adapter cannot grant positive trading eligibility |

Every case calls the real D4B2 writer on a fresh disposable synthetic SQLite database with two synthetic seed rows and one candidate date. SQLite authorizers deny and record any attempted INSERT/UPDATE/DELETE on `prices` or `market_price_provenance`; every case records **zero attempts**. Before/after rows and provenance match, no manifest is created, a rejected receipt retains the outcome, `research_eligible=false`, and foreign-key checks are clean.

A separate synthetic lifecycle control proves the same calendar, complete candidate fingerprint, stable observations and watermark pass the existing completed-session and operational admission gates. No control write is executed and no synthetic control qualifies CTR data. This establishes that rejection is caused by the evidence boundary, not an unrelated fixture failure.

All mutations use test data under the disposable visualization test directory. No canonical SQLite connection or backup was needed, and no database file was copied. Canonical `data/market.db` was read only for file hashing; before/after SHA-256:

`f771b314ef2094373c3753def45223dd6e61aec84e15a369fcd52da585265fb6`

The 18 initial untracked artifacts remain byte-identical and outside this change. Tracked worktree and index remain unchanged; only the three new files above are added to the untracked worktree. Bundled Python 3.12 was used with repository pytest dependencies, bytecode/cache/plugin autoload disabled and new explicit temporary directories. No provider fetch, Update/Daily/Scan, paper/Forward/Telegram/broker, backtest or WFO was run.

## Limits and remaining blockers

NOT_TRADING on 17 February applies the explicit first-trading boundary `2022-02-17 < 2022-02-23`; the original notice does not literally state the target date. ISIN remains a VSDC claim, not a HOSE notice claim. The adapter trusts the reviewed packet's mapping and retained bytes; it does not OCR or requalify the manual transcription.

Historical availability remains UNVERIFIED; packet as-of admissibility and research eligibility remain false. The existing schema has no historical-availability or ISIN field: the packet retains those limitations, and this denial-only adapter never returns TRADING_CONFIRMED. Current snapshots do not prove historical byte availability or complete amendment coverage. No UPCoM interval, exchange-wide calendar, provider completion, actual price row or price basis is qualified.

The adapter is available only to explicit offline shadow callers; no production entrypoint is connected. D4B1 upsert, legacy update/backfill/direct/maintenance bypasses, numeric detection tolerance `1e-9`, bounded revision-watch coverage, basis/version pinning, source resolver/universe coverage and incident exclusion remain outside this change. Old receipts are not rewritten or requalified. Real ten-session shadow and production/research readiness remain BLOCKED.

## Subsequent focused checkpoint review

The preceding HEAD/status and no-commit statements describe the integration turn, before this checkpoint. This review retains the packet and all 18 outside artifacts unchanged. Hash matching is an integrity check against a caller-supplied reviewed version, not an authority registry or independent approval of a claim. The only existing packet caller is the focused test with the literal reviewed hash; new packet versions require independent review before a caller changes that pin. No positive trading fallback exists: valid input returns NOT_TRADING only, invalid input returns unattributable UNKNOWN, and the existing completion/admission gates reject or remain unresolved before publication evidence can admit it.

Two direct defects were reproduced: a missing pin (`None`) raised TypeError before the fail-closed handler, and a malformed relocation map raised AttributeError. The minimal fix checks pin type and handles malformed input attributes as UNKNOWN; both regressions exercise the real shadow writer with zero attempted price/provenance mutations and false research eligibility. The two new focused cases pass. The existing 14 passing cases (including the subsequently strengthened same-length corruption case) were reused, not rerun. No real source, policy, schema, production writer, tolerance or historical availability changed. Exactly this report, the packet adapter and its focused test file are included in the checkpoint commit.
