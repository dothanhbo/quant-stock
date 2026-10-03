# D4B2.7B Static Security Review and Offline Shadow Preparation

Review date: 2026-10-03. Scope: installed `vnstock==4.0.2` and the static
`Quote.history()` → KBS adapter → `vnai.optimize_execution()` → shared HTTP
client path. No package module was imported or executed during this review.

## Determination

Status is **PARTIAL**. The reviewed package bytes match the previously captured
official wheel, but source inspection does not reduce `Quote.history()` to one
KBS HTTP request. Call-time `vnai` setup can inspect the environment and
filesystem, write under the user profile, start background/exit work, and make
ancillary network requests. `VNSTOCK_TELEMETRY=off` suppresses relay dispatch,
not the independently implemented promotional-content and quota/license paths.
Direct execution is therefore not suitable for the first isolated observation.

Prefer a separately reviewed HTTP-only observation client, but only after the
KBS endpoint's access terms and redistribution/retention rules are confirmed.
It must use the normal public access mechanism and must not bypass licensing,
authentication, rate limits, or provider restrictions.

The installed `vnstock==4.0.2` wheel identity previously qualified in D4B2.7A
is SHA-256 `4a32c169b934bc130dcb0267ffd01635d3e7e3f12cf485c82909e38a9cdf9489`.
This review re-read files without importing them. Key installed hashes are:

| File | SHA-256 |
|---|---|
| KBS `quote.py` | `eddeac3391b31decbb0569c6f9028a61bf6a0437332b1ce492eb1b734b47a088` |
| KBS `const.py` | `7b43bd33d527f00a466dad28c0c9172c303338d526edae0cc4134a637973cffa` |
| shared `client.py` | `da638430637a6f638692bb4cc282fcefdc2c8fdf0746c683f437f4c9514ef23f` |
| `vnstock` METADATA | `d4a2e65308182365ac577db6d0bd04c48ffbb9c55b54cccf9a6df89821f29ac1` |
| installed `vnstock` RECORD | `1f49e24f6b27f71fa9385485620fd5d6ec9df532d2abec25932b07cbd5bc167` |
| `vnai==2.6.0` METADATA | `9ba06d7a631784b4e75bdfa9739a272149d81cadfc73727c0f64a5ae03a5823b` |
| installed `vnai` RECORD | `9261d907dfdde644ac162bce93f603cb6e88562427842b43651f3a9b4020015f` |

## Static findings

Labels mean: `VERIFIED_FROM_SOURCE` is directly present on the reviewed path;
`POTENTIAL` is conditional on configuration/state; `NOT_OBSERVED` means the
review found no such construct and is not a proof of absence; `UNRESOLVED`
requires execution-independent evidence not currently available.

| Area | Status | Finding |
|---|---|---|
| KBS history destination | VERIFIED_FROM_SOURCE | GET to `https://kbbuddywts.kbsec.com.vn/iis-server/investment/stocks/{symbol}/data_{interval}` or `/index/{symbol}/data_{interval}`, with `sdate` and `edate`. |
| Import-time update check | VERIFIED_FROM_SOURCE | Importing top-level `vnstock` invokes `update_notice(verbose=False)`. When dependency checks report no critical issue, it runs `python -m pip list` and GETs `https://pypi.org/pypi/vnstock/json` and `/vnai/json`. If optional `vnii` or `vnstock_installer` is installed, it instead checks `https://vnstocks.com/api/simple/vnii` or `/vnstock-installer`; neither optional distribution is installed here. Exceptions are suppressed. |
| Proxy discovery/test | POTENTIAL | Non-default proxy mode can GET `https://api.proxyscrape.com/v4/free-proxy-list/get`, test `https://httpbin.org/ip`, or route through user-supplied proxy URLs. Default KBS construction is direct unless a proxy list/config is supplied. |
| Redirect expansion | POTENTIAL | The shared `requests.get` call does not disable redirects. A provider, proxy, or compromised endpoint can therefore redirect outside the original hostname unless a later client explicitly blocks and validates redirects. |
| Telemetry relay | VERIFIED_FROM_SOURCE | `vnai.flow.relay` can POST to `https://hq.vnstocks.com/analytics`; `VNSTOCK_TELEMETRY=off` disables this relay dispatch. |
| Promotional content | VERIFIED_FROM_SOURCE | Startup/display paths can GET `https://hq.vnstocks.com/content-delivery` independently of the telemetry switch. Returned image/target URLs are content; automatic retrieval of those target URLs was not observed on this terminal path. |
| Quota/license verification | VERIFIED_FROM_SOURCE | The optimize wrapper lazily imports quota code; usage/license synchronization can POST to `https://vnstocks.com/api/vnstock/license/verify`, including device/package/version/usage fields and an API key when present. |
| Optional subscription integration | UNRESOLVED | Promo tier detection conditionally imports `vnii`, but no `vnii` distribution is installed, so the reviewed integration returns before calling it. Network behavior would need a separate review if `vnii` were later installed. A separate profile-sync method contains `https://api.vnstocks.com/v1/user/profile/sync`, but no call to that method was observed on the history path. |
| API-key registration | NOT_OBSERVED | `https://vnstocks.com/api/vnstock/auth/device-register` exists in the auth utility, but ordinary `Quote.history()` does not call the explicit API-key setup function. |
| Import-time network | VERIFIED_FROM_SOURCE | The top-level update-notice path can contact PyPI before `Quote.history()` is called. Whether it sends requests depends on local compatibility results, so importing in a networked process is not side-effect-free. |
| Call-time side effects | VERIFIED_FROM_SOURCE | First optimized call initializes `vnai`, registers exit work, may start background work, shows startup content, performs quota accounting, and then invokes the KBS function. Exceptions in setup are broadly suppressed. |
| Filesystem | VERIFIED_FROM_SOURCE | Reads/writes include `~/.vnstock`, device ID/registry, API-key/config/cache/state files; package/profile inspection also examines working directories, Git state and candidate license files. |
| Environment | VERIFIED_FROM_SOURCE | Reads telemetry/API-key/IDE/runtime variables and scans environment values for commercial-context indicators. Raw environment-value exfiltration was not observed in the reviewed relay payload construction. |
| Credentials | VERIFIED_FROM_SOURCE | Reads `VNSTOCK_API_KEY` and `~/.vnstock/api_key.json`; quota/license calls can transmit the API key. User proxy URLs may embed credentials. KBS history headers themselves do not contain a KBS account token in the reviewed constructor. |
| Dynamic execution | NOT_OBSERVED | No `eval`, `exec`, `compile`, pickle/marshal load, `ctypes`, downloaded-code execution, or shell execution was observed on the path. |
| Dynamic loading/reflection | VERIFIED_FROM_SOURCE | Fixed lazy imports load quota/provider modules; provider dispatch uses registries/introspection; optional `vnii` is imported when present. System profiling runs fixed Git subprocess commands, not downloaded commands. |
| Response preservation | VERIFIED_FROM_SOURCE | Shared client parses JSON. KBS history extracts the interval array and normalizes it; by default it does not preserve HTTP headers or the complete response envelope as raw provenance. |
| Quarantine reason | UNRESOLVED | PyPI exposes the project status as quarantined but no attributable quarantine reason. Quarantine alone is not treated as a malware finding. |

Relevant transitive components on this path are `vnai`, `requests` and its
HTTP/TLS stack (`urllib3`, `certifi`, `idna`, `charset-normalizer`), `pydantic`,
`tenacity`, `pandas`/`numpy`, `psutil`, packaging/metadata inspection, and
standard-library JSON, threading, subprocess, filesystem and platform modules.
The optional `vnii` behavior remains unresolved. The installed distribution's
previous integrity qualification remains evidence of byte identity, not of
behavioral safety.

## Calendar evidence

The content-addressed evidence index is
`research/market_calendar_evidence/2026/calendar_sources.json`. It records HNX
notice 5305 (HNX/UPCoM baseline), HNX notice 5680 (amendment), and HOSE notice
2410 (amendment), using official URLs, byte lengths and SHA-256 values. The
original official bytes for HOSE notice 2294 remain missing, so the HOSE 2026
baseline remains unresolved. Notice 2410's reference to 2294 establishes that
the baseline existed; it is not a substitute for its bytes.

Hashes are immutable identities, while remote URLs are not retention promises.
No source document is copied into the repository until redistribution/local
retention rights are established. The manifest defines a per-run official
notice check for amendments and exceptional closures; a failed or conflicting
check fails closed.

## Offline observation contract

`quantlab/offline_market_observation.py` accepts only a supplied synthetic
contract with explicit symbol-to-venue mapping, calendar snapshot, transport
mode `DISABLED`, and an explicitly present publication delay (which may be
`null`). Each observation retains the exact fixture string, raw SHA-256,
normalized row fingerprint, UTC observation time, and separate raw/normalized
provenance. Every invocation creates a unique evidence directory with a
contract snapshot, JSON Lines observations, and a result file.

The result reports comparisons only. Admission and completed-session fields are
always null/false. The module has no provider, transport, database, admission,
transaction, shadow-adapter or production-updater import.

## Network-enabled pilot prerequisites

1. Obtain and hash the official original bytes for HOSE 2294, or exclude HOSE.
2. Recheck official HNX, UPCoM, HOSE and State Securities Commission notices for
   amendments/exceptional closures; retain an attributable query record.
3. Obtain documented KBS authorization/access terms, rate limits, payload
   retention permission and endpoint stability. Do not derive permission from
   endpoint accessibility.
4. Implement and review a minimal HTTP-only transport in an OS/process sandbox
   with an outbound allowlist limited to the approved KBS host, no proxy, no
   ambient credentials, a fresh empty home, and package imports blocked.
5. Capture exact response bytes plus status, selected headers, request identity,
   TLS/runtime metadata, observation UTC time and SHA-256 before normalization.
6. Run observation-only for one explicitly mapped symbol/venue/session into a
   unique isolated evidence directory. Make no admission decision and perform
   no SQLite write.
7. Use two independently timed observations only to measure stability. A delay
   must be empirically qualified; identical fingerprints do not prove full
   history, completion, calendar validity, or research-label eligibility.
