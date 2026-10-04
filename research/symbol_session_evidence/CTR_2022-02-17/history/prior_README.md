# CTR / HOSE / 2022-02-17: partial source qualification

Collected and reviewed 2026-10-04. **Tradability and operational admission remain UNRESOLVED; research_eligible=false.** Exactly one target symbol-session is mapped. The 2022-02-23 date is a contextual source claim, not a second qualified session.

CTR was selected because the official depository transfer notice already identified a real security and an effective boundary. It was not selected to obtain PASS. This evidence supports identity and a narrowly defined transfer claim, not a complete lifecycle snapshot.

## Retained sources

1. [VSD/VSDC transfer notice, article 147589](https://vsdc.vn/vi/ad/147589). Original HTTP body: `vsdc_transfer.html`, 72,078 bytes. Collected **2026-10-04T14:05:39.045723Z**. SHA-256:

   `94d8f196adfdf16e437f0e5f1ea79a1afab0bafeae92a0e2cfc84cdf2f519285`

   Direct claims: CTR; ISIN **VN000000CTR4**; issuer **Tổng Công ty cổ phần Công trình Viettel**; common shares; UpCOM → HOSE registration/depository data transfer effective **2022-02-17 on the VSD system**. The article displays an update time of **2022-01-27 12:20:26**, without a stated timezone. This is a displayed timestamp, not independently verified original publication time.

2. [HOSE Annual Report 2022](https://staticfile.hsx.vn/Uploads/UploadDocuments/1641900/Bao%20cao%20thuong%20nien%202022_tv.pdf). Original HTTP PDF body, 11,923,116 bytes. Collected **2026-10-04T14:08:35.306002Z**. SHA-256:

   `5ebc07d70af3a540469a5954a4abd5a75a9e3b8d64ba35fda42095b49231460f`

   Snapshot: `C:\Users\hello\.codex\visualizations\2026\10\04\01a10720-75cf-7d50-9214-075861ee51ec\d4-ctr-session-20220217\hose_annual_report_2022.pdf`. Physical PDF page 47, printed page 97, newly listed companies table, row 3 CTR: date **23/2/2022**, under **Ngày giao dịch / Listing date**. Visual inspection confirmed that the adjacent fund table's **First trading day** heading belongs to a different table. Preserve the actual CTR heading; do not reinterpret it as an original first-trading decision.

   PDF creation/modification metadata is June 2023. The verified publication time is unknown; metadata is not publication evidence. `hose_page47.png` in the same snapshot directory records the rendered inspection. Keep the PDF companion when relocating this packet; the focused checker fails if it is missing.

The SSC archive retrieval timed out. Its attempt is retained in `collection_manifest.json`; no snapshot or hash exists and no supported claim relies on its search-index text. No provider OHLCV, CafeF parity or synthetic status qualifies the real session.

## Gate mapping and limitations

| Gate | Result from this packet |
|---|---|
| Stable security identity and transfer destination/effective date | Supported directly, restricted to the notice's registration/depository scope. This is not proof of permission to trade. |
| Existing `SymbolSessionEvidence` schema | Constructible with attributable source/hash references and status **UNKNOWN**. ISIN, effective scope, publication/collection times and point-in-time limitations remain in the packet because the class has no fields for them. Nonempty references only satisfy code attribution syntax. |
| Policy acceptance 1: real CTR inactive-date rejection | BLOCKED: complete tradability interval is not established. No assertion of NOT_TRADING or the full UPCoM/HOSE gap is made. |
| Policy acceptance 2: unresolved lifecycle fail-closed | Focused negative check PASS; real lifecycle qualification remains BLOCKED. Missing calendar returns `CALENDAR_EVIDENCE_MISSING`; with a synthetic open calendar, UNKNOWN returns `SYMBOL_SESSION_UNKNOWN` even before stable observations/watermark can admit it. No database access or writes occur. |
| Shadow completed-session 1: exchange calendar | BLOCKED: no qualified HOSE 2022 calendar/exceptional-closure coverage. A venue being open does not establish CTR tradability. |
| Shadow completed-session 3-5: publication, candidate row and completion evidence | BLOCKED: no real provider observations, watermark, measured approved delay or candidate-row corroboration. |
| Research / historical point-in-time | BLOCKED: today's snapshots do not establish which bytes/claims were available on 2022-02-17. Basis/version/event/window lineage and incident exclusion are not established. |

Missing documents are specified individually in `evidence_packet.json`: the original HOSE first-trading announcement with document number, issue date, explicit first date and applicable amendments/status notices; the HNX cancellation/last-trading notice before asserting an all-venue gap; official calendar/closure coverage; independently dated historical source versions; separate provider completion and basis/event/incident lineage evidence. No document number has been guessed.

No policy, schema, production code, prior receipt or old evidence was edited. D4B1 upsert and production/maintenance bypasses, revision-watch coverage and the `1e-9` comparator tolerance remain as described in the two detected-revision reports. This packet is not wired to any writer or eligibility promotion.

## Focused checks

`check_packet.py`: **3 tests PASS**. Checks original source lengths/SHA-256, exact article field bindings, the HOSE CTR row/column and PDF metadata, schema/bundle mapping, and fail-closed behavior. Positive calendar/provider inputs are clearly synthetic negative controls for this same target session; they are not real evidence. Results and packet hash: `focused_check_results.json`. No existing test suite was rerun, and no DB or network is used by this checker.

## Exactly one next step

Acquire and snapshot the **original HOSE CTR first-trading announcement**, retaining document number, issue date, explicit first-trading date and SHA-256, then review it against this one session while keeping writers inactive. This step alone does not establish historical availability or authorize a ten-session shadow.
