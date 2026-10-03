# CafeF RAW versus canonical EOD parity — 2026-10-01

Status: `REFERENCE_SOURCE_USABLE=YES_BOUNDED`; `OPERATIONAL_ADMISSION_READY=NO`; `RESEARCH_ELIGIBLE=NO`.

Review date: 2026-10-03 (Asia/Bangkok). This was a read-only comparison of original externally supplied CafeF archives against canonical `data/market.db`. The database was opened read-only with `PRAGMA query_only=ON`; no provider was called and no historical value was changed.

Canonical database SHA256 before review: `F771B314EF2094373C3753DEF45223DD6E61AEC84E15A369FCD52DA585265FB6`.

## Source identity

The original ZIP names were discovered inside their dated evidence directories and their bytes matched the hashes recorded in the external README.

| Session | Original archive | Bytes | SHA256 | Rows | HSX | HNX | UPCoM |
|---|---|---:|---|---:|---:|---:|---:|
| 2026-10-01 | `2026-10-01/CafeF.SolieuGD.Raw.01102026.zip` | 14,693 | `8476EEF9FEE467536A0EF3C7F1D76CD289E7203591EB8D2C42E2FFDC2FFF899D` | 1,170 | 674 | 191 | 305 |
| 2026-10-02 | `2026-10-02/CafeF.SolieuGD.Raw.02102026.zip` | 15,437 | `E86F19DF3E97C4701480F19CB1E1559E1D3BC53636461024037F8D6E3A983300` | 1,239 | 680 | 204 | 355 |

Embedded member identities:

| Session | Venue | Member SHA256 |
|---|---|---|
| 2026-10-01 | HSX | `6796DF18FB104E94487AFF1B6340887CB09C3A9973FB1C92E281CEA2CB30F1EE` |
| 2026-10-01 | HNX | `1BB60D8AE27AE89E41F0714F9232645BB8071EE3F1E5075E558A4073B4968B2D` |
| 2026-10-01 | UPCOM | `5F03480226D5929B009EA58DAB9B3E778164F1068C1BD184C4483BD32E9F40FA` |
| 2026-10-02 | HSX | `3723ABDC743C35F43F52D8AA3A14E5B833CECAAEAA1D48B675909CEBF75970CA` |
| 2026-10-02 | HNX | `26A2295FCD34D18F3EF5E451E66F23C8B61DD37B49B69C8CDBC337370C60C652` |
| 2026-10-02 | UPCOM | `8ACA35B2F0FB5A3F7B393B6A12F5C8A7A71B89F0B3CA87138D7F3D56D6E01FD2` |

The existing CafeF validator accepted both originals: exact session/member naming, supported schema, unique ticker/session keys, and structurally valid OHLCV. The prepared cross-check CSV has SHA256 `D377F286BC73CDB89AFC8EFDC3229E728A563BC72A350CDDF1C197191F3404D0`; all venue, close, and volume fields for the 100-symbol subset reproduce the original ZIP members. It was not used as primary evidence. The external README SHA256 is `B26A627736276D166A7C38CCF6643298AA06D40BF8BA0C3AE8AC671918F5650B`.

## Same-session parity

The comparison universe is the 100 equities in canonical session 2026-10-01, excluding `VNINDEX`. All 100 occur in both CafeF archives and are claimed under HSX in both.

Prices were compared without rescaling. Exact decimal equality was checked first; a non-exact difference of at most `0.0005` would have been classified as display precision/rounding because it is half the smallest observed `0.001` comparison unit. Integer volume required exact equality.

| Measure | Result |
|---|---:|
| Canonical symbols compared | 100 |
| CafeF 2026-10-01 observations present | 100 |
| Exact close matches | 100 |
| Non-exact matches within `0.0005` | 0 |
| Material close differences | 0 |
| Exact full OHLC matches | 100 |
| Exact volume matches | 100 |
| Volume mismatches | 0 |
| Missing canonical or CafeF observations | 0 |
| Potential scale/unit differences observed | 0 |

Per-symbol result: exact OHLCV parity for `ACB, ANV, BAF, BCM, BID, BMP, BSI, BSR, BVH, BWE, CII, CMG, CTD, CTG, CTR, CTS, DBC, DCM, DGW, DIG, DPM, DSE, DXG, EIB, EVF, FPT, FRT, FTS, GAS, GEE, GEX, GMD, GVR, HAG, HCM, HDB, HDG, HHV, HPG, HSG, HT1, KBC, KDC, KDH, KOS, LPB, MBB, MCH, MSB, MSN, MWG, NAB, NKG, NLG, NT2, NVL, OCB, PAN, PC1, PDR, PHR, PLX, PNJ, POW, PVD, PVT, REE, SAB, SBT, SHB, SIP, SJS, SSB, SSI, STB, TAL, TCB, TCH, TCX, TPB, VCB, VCG, VCI, VCK, VGC, VHC, VHM, VIB, VIC, VIX, VJC, VND, VNM, VPB, VPI, VPL, VPX, VRE, VSC, VTP`.

The numerical volume parity supports the practical conclusion that these two files and canonical rows use the same observed share-count scale for this session. It does not replace provider documentation or prove the unit semantics for other dates or security types.

The full CafeF files contain 1,070 additional identifiers on October 1 and 1,139 on October 2. They were not treated as equities or bootstrap candidates: the files do not provide an authoritative security type or lifecycle register. `VNINDEX` is outside the supplied CafeF security rows and still requires a separate index source.

## Adjacent-session review

A transparent absolute simple-return threshold of 5% was used only to prioritize monitoring. It is not a corruption or corporate-action rule.

| Ticker | CafeF close 2026-10-01 | CafeF close 2026-10-02 | Simple return | Volume 2026-10-01 | Volume 2026-10-02 |
|---|---:|---:|---:|---:|---:|
| TPB | 14.40 | 11.90 | -17.361111% | 16,555,800 | 11,178,200 |
| KOS | 15.80 | 14.70 | -6.962025% | 2,500 | 5,000 |
| ANV | 15.90 | 17.00 | +6.918239% | 432,500 | 2,821,200 |
| PNJ | 24.75 | 23.05 | -6.868687% | 1,677,600 | 76,068,800 |

### TPB

Confirmed observations:

- CafeF October 1 OHLCV is `14.45 / 14.65 / 14.40 / 14.40 / 16,555,800`, exactly matching canonical October 1.
- CafeF October 2 OHLCV is `12.30 / 12.30 / 11.75 / 11.90 / 11,178,200`.
- The close-to-close change is `-17.361111%`.

Status: `REVIEW_REQUIRED`. The two RAW-labelled original files establish the observed values, but they do not establish why the discontinuity occurred. A corporate action, reference-price treatment, or price-basis event is possible; none is verified here. TPB must not pass a boundary-discontinuity control or be interpreted as an economic shareholder return without an attributable issuer/exchange/VSDC event and source adjustment semantics.

## Decision

`REFERENCE_SOURCE_USABLE=YES_BOUNDED`:

- CafeF RAW is practical as an externally supplied, content-addressed monitoring/reference file for the current 100-equity universe.
- It provides complete two-session coverage for those names and exact independent-file parity with canonical October 1 OHLCV.
- “Independent” here means a separately retained file channel. Its upstream independence from the source that produced legacy canonical data is not established.

`OPERATIONAL_ADMISSION_READY=NO`:

- The two consecutive sessions are not two independently timed observations of the October 2 publication and do not prove publication completion.
- No attributable venue completed-session snapshot or per-symbol `TRADING_CONFIRMED` lifecycle snapshot was supplied.
- The current one-day manual adapter does not combine the two immutable archives into an overlap-bearing batch; no missing overlap was manufactured.
- RAW is a source label, not verified adjustment or corporate-action provenance.
- TPB requires attributable boundary-event review before any operational append.

`RESEARCH_ELIGIBLE=NO`:

- One exact reference session does not verify whole-history basis, revisions, corporate-action treatment, or upstream lineage.
- No historical row or existing research artifact is promoted by this result.

Minimum conditions for a controlled operational shadow are: retain both original archive and member hashes; obtain an authoritative October 2 venue calendar snapshot and symbol-session lifecycle evidence for every candidate; establish CafeF publication completion through an attributable watermark or qualified same-session observation policy; bind a stable source/price-basis identity; form a reviewed two-session overlap batch without altering source rows; and resolve every D4A boundary finding, including TPB, with attributable evidence. All work must remain on a disposable clone until those conditions pass.
