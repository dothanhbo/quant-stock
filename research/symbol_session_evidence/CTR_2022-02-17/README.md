# CTR / HOSE / 2022-02-17: original first-trading notice qualified

Collected and reviewed 2026-10-04. **This target maps NOT_TRADING: its HOSE first official trading date had not arrived. Operational/research eligibility remains false; research_eligible=false.** Exactly one target symbol-session is mapped. The 2022-02-23 start date is a document claim, not a second qualified session. No real calendar or admission/write attempt was supplied; the completed-session check without calendar remains UNRESOLVED.

CTR was selected in the prior review because the official depository transfer notice identified a real security and an effective boundary. This follow-up adds the original HOSE trading-start notice for that same target. No selection or policy change was made to obtain PASS; full lifecycle/amendment/universe qualification remains incomplete.

## Original HOSE notice added in this follow-up

**191/TB-SGDHCM, issued 2022-02-15**, one-page signed/stamped scan, signatory Trần Anh Đào. [Original HOSE PDF](https://staticfile.hsx.vn/Uploads/News/6571266f2f084c4e80b7ca12a4e10f63/20220215_20220215%20-%20CTR%20-%20TB%20ngay%20giao%20dich%20dau%20tien.pdf) and [issuer-published copy](https://viettelconstruction.com.vn/wp-content/uploads.bak/2022/02/20220215_20220215-CTR-TB-ngay-giao-dich-dau-tien.pdf) are byte-identical at collection: **234,905 bytes**, SHA-256:

`a8ec7676fb47db068dc663d356d1a1eac6e4b7be17a296a6895ded74e7fb28e5`

Collected from HOSE **2026-10-04T14:39:40.276027Z** and from the issuer **2026-10-04T14:36:43.970233Z**. Original bytes, download metadata and source binding are retained in `evidence_packet.json` / `collection_manifest.json`; snapshot files are in `C:\Users\hello\.codex\visualizations\2026\10\04\01a10720-75cf-7d50-9214-075861ee51ec\d4-ctr-first-trading-notice\` as `hose-original-0.pdf` and `issuer-original-retry-0.pdf`.

Manual visual review of the complete scan and enlarged header confirmed the handwritten document number and the following exact claim-supporting text on page 1:

> Về việc niêm yết và ngày giao dịch đầu tiên của cổ phiếu
>
> Ngày chính thức giao dịch: 23/02/2022

The PDF has no embedded text layer; the excerpt is a manual transcription bound to its original SHA-256, not OCR output. The notice expressly identifies **CTR**, **Tổng Công ty cổ phần Công trình Viettel**, and common shares. It does **not** state ISIN; **VN000000CTR4 remains sourced to VSDC**, joined by matching symbol and issuer.

The notice distinguishes listing effectiveness (**2021-12-27**, with a reference to decision 727/QĐ-SGDHCM) from the official first trading date (**2022-02-23**). VSDC's **2022-02-17** depository transfer is a third, separately scoped fact. The referenced listing decision was not separately acquired or qualified.

For **CTR/HOSE/2022-02-17**, the explicit first-trading boundary establishes that official trading had not yet begun: `2022-02-17 < 2022-02-23`. Mapping is **NOT_TRADING / listed but not yet trading according to this notice**. The document does not literally name 17 February; this date comparison is recorded transparently. It does not assert suspension, absence of listing, UPCoM status, a full transfer gap, venue-wide calendar, provider completion or price basis. Any contrary official amendment requires re-review; complete historical amendment coverage is not certified.

The [issuer disclosure index](https://viettelconstruction.com.vn/quan-he-co-dong/cong-bo-thong-tin/) directly links this PDF under the relevant title and displays `2022-02-16T03:37:19+07:00`. The [HOSE notice API](https://api.hsx.vn/n/api/v1/1/news/1500539) identifies the same CTR notice; its attachment API supplies the original HOSE PDF path. These current index/API snapshots establish the retrieval chain, not historical availability. Current PDF metadata, scanned signature/stamp and matching bytes are not an independently dated archive or cryptographic signature verification.

## Prior evidence retained unchanged

1. [VSD/VSDC transfer notice, article 147589](https://vsdc.vn/vi/ad/147589). Original HTTP body: `vsdc_transfer.html`, 72,078 bytes. Collected **2026-10-04T14:05:39.045723Z**. SHA-256:

   `94d8f196adfdf16e437f0e5f1ea79a1afab0bafeae92a0e2cfc84cdf2f519285`

   Direct claims: CTR; ISIN **VN000000CTR4**; issuer **Tổng Công ty cổ phần Công trình Viettel**; common shares; UpCOM → HOSE registration/depository data transfer effective **2022-02-17 on the VSD system**. The article displays an update time of **2022-01-27 12:20:26**, without a stated timezone. This is a displayed timestamp, not independently verified original publication time.

2. [HOSE Annual Report 2022](https://staticfile.hsx.vn/Uploads/UploadDocuments/1641900/Bao%20cao%20thuong%20nien%202022_tv.pdf). Original HTTP PDF body, 11,923,116 bytes. Collected **2026-10-04T14:08:35.306002Z**. SHA-256:

   `5ebc07d70af3a540469a5954a4abd5a75a9e3b8d64ba35fda42095b49231460f`

   Snapshot: `C:\Users\hello\.codex\visualizations\2026\10\04\01a10720-75cf-7d50-9214-075861ee51ec\d4-ctr-session-20220217\hose_annual_report_2022.pdf`. Physical PDF page 47, printed page 97, newly listed companies table, row 3 CTR: date **23/2/2022**, under **Ngày giao dịch / Listing date**. Visual inspection confirmed that the adjacent fund table's **First trading day** heading belongs to a different table. Preserve the actual CTR heading; do not reinterpret it as an original first-trading decision.

   PDF creation/modification metadata is June 2023. The verified publication time is unknown; metadata is not publication evidence. `hose_page47.png` in the same snapshot directory records the rendered inspection. Keep the PDF companion when relocating this packet; the focused checker fails if it is missing.

The earlier SSC archive retrieval timed out. Its original attempt is retained unchanged; no snapshot or hash exists and no supported claim relies on its search-index text. Initial issuer TLS timeouts, public-cookie challenge responses and successful subsequent discovery are separately recorded in the follow-up manifest. Newspaper, annual-report and ceremony/press-release search hits were not substituted for the original notice. No provider OHLCV or CafeF parity was used.

## Gate mapping and limitations

| Gate | Result from this packet |
|---|---|
| Stable security identity and transfer destination/effective date | Supported directly, restricted to the notice's registration/depository scope. This is not proof of permission to trade. |
| Existing `SymbolSessionEvidence` schema | This target maps **NOT_TRADING**, with references to the original notice and VSDC identity. ISIN, effective scope, publication/collection times and point-in-time limitations remain in the packet because the class has no fields for them. Nonempty references alone only satisfy attribution syntax. |
| Policy acceptance 1: real CTR inactive-date rejection | This one HOSE start-date boundary is supported. The full CTR transfer gap, other dates and complete real-calendar/provider exercise remain BLOCKED. |
| Policy acceptance 2: lifecycle fail-closed | Focused check PASS: missing calendar returns `CALENDAR_EVIDENCE_MISSING`; with a synthetic open calendar, NOT_TRADING returns `REJECTED/SYMBOL_NOT_TRADING` before stable observations/watermark can admit it. Removing the new status evidence in a negative control leaves UNKNOWN/UNRESOLVED. Real-universe coverage/resolver integration remain BLOCKED. No database access or writes occur. |
| Shadow completed-session 1: exchange calendar | BLOCKED: no qualified HOSE 2022 calendar/exceptional-closure coverage. A venue being open does not establish CTR tradability. |
| Shadow completed-session 3-5: publication, candidate row and completion evidence | BLOCKED: no real provider observations, watermark, measured approved delay or candidate-row corroboration. |
| Research / historical point-in-time | BLOCKED: today's snapshots do not establish which bytes/claims were available on 2022-02-17. Basis/version/event/window lineage and incident exclusion are not established. |

The original HOSE first-trading notice is now retained with its number, issue date and explicit first date. Still unresolved: independently dated historical source versions and complete applicable amendment coverage; HNX cancellation/last-trading evidence before asserting an all-venue gap; official calendar/closure coverage; separate provider completion and basis/event/incident lineage. These are not inferred from this notice. Today's snapshots do not establish identical bytes available at the historical session.

Only this packet, its manifest and README were updated. Previous versions of all three were retained byte-for-byte as `prior_*` in the follow-up snapshot directory; previous source objects, claims, raw snapshots, check script/results, code changes and other artifacts remain unchanged. D4B1 upsert and production/maintenance bypasses, revision-watch coverage and the `1e-9` comparator tolerance remain as described in the two detected-revision reports. This packet is not wired to any writer or eligibility promotion.

## Focused checks

Previous `check_packet.py` and `focused_check_results.json` are preserved as historical evidence for the prior UNKNOWN packet hash; they are not a current validation of the updated packet and were not rerun. Current focused checks are `check_notice_packet.py` / `notice_packet_check_results.json` in the follow-up snapshot directory: **3 tests PASS**, covering source hashes and byte agreement, notice/identity/target mapping, and fail-closed behavior. Manual scan transcription is identified explicitly rather than represented as machine-extracted text. Positive calendar/provider inputs are synthetic negative controls only. No unrelated tests/rehearsal, DB access or network fetch is used by the checker.

## Offline repository checkpoint

The already-collected primary notice, issuer copy, disclosure index, HOSE API bodies and prior annual-report context are retained byte-for-byte under `sources/`. Original source objects and their collection paths remain unchanged. `checkpoint.snapshot_paths` in the packet/manifest maps those historical locations to repository-relative copies; the checker requires these copies and never falls back to an external snapshot. This relocation does not change collection time, source hash or historical availability (**UNVERIFIED**).

`history/` retains the original UNKNOWN packet/manifest/README and the notice packet/results before relocation, byte-for-byte. `check_packet.py` and `focused_check_results.json` are historical UNKNOWN-packet artifacts only; do not run that checker against the current packet. Current validation is `check_notice_packet.py` with `notice_packet_check_results.json` beside this README. Run it from any directory with a Python runtime containing pypdf and the repository dependencies; set `PYTHONDONTWRITEBYTECODE=1`. It performs offline hash/claim/fail-closed checks only. Discovery/challenge/cookie bodies, fetch scripts and inspection images remain outside the commit.

No staging logic, source claim, policy, tolerance, writer wiring or eligibility was changed during this checkpoint review. Prior prose above records the earlier collection turn; this section records the subsequent packaging correction.

## Exactly one next step

Acquire an **independently dated historical archive/publication record for 191/TB-SGDHCM**, verifying its version and any linked amendments. Keep writers inactive and research_eligible=false; this evidence step does not authorize a ten-session shadow.
