# D4 — KBS pilot source qualification

Rà static ban đầu và bổ sung tài liệu ngày 2026-10-05 tại `main`, HEAD `ec3566ab4db4d28667c7fc3bbb6d3de9eca342c3`. **Chưa đủ cơ sở mở network pilot; PROPOSAL_ONLY. Zero market-data/backend requests**, không import/execute vnstock/vnai, không đọc API key/profile hoặc suy tier từ cấu hình. Phần trace bên dưới giữ bằng chứng code; phần cuối bổ sung một lượt tìm tài liệu chính thức có giới hạn và snapshot tài liệu, không snapshot dữ liệu giá. [Memo pilot](D4_PERSONAL_EOD_MINIMUM_DECISION_MEMO.md) là kế hoạch có điều kiện, chưa schedule/activation.

## Trace nguồn thực tế

| Đoạn đường | File/function đã đọc | Hành vi thấy trong code |
|---|---|---|
| App entrypoint | [Update](../scripts/update_data.py), `update_symbol` / `calculate_start_date` | Import `vnstock.api.quote.Quote`, `source="KBS"`, interval `1D`; start=latest legacy−7 ngày, end=`datetime.now()`, rồi `save_price_data`. Không dùng entrypoint này cho pilot; nó ghi DB và chưa pin end=T |
| Wrapper → registry | [API Quote](../.venv/Lib/site-packages/vnstock/api/quote.py), `Quote.__init__`, `history`, `_delegate_to_provider`; [BaseAdapter](../.venv/Lib/site-packages/vnstock/base.py) | Lazy explorer load; registry chọn provider. API `history` có `optimize_execution("API")` và retry (config 3 attempts), delegate sang provider `history` có `optimize_execution("KBS")`. Không phải một lời gọi thuần HTTP |
| Provider thực | [KBS Quote](../.venv/Lib/site-packages/vnstock/explorer/kbs/quote.py), `Quote.history`, đăng ký `ProviderRegistry.register('quote','kbs',Quote)` | GET `https://kbbuddywts.kbsec.com.vn/iis-server/investment/stocks/{symbol}/data_day`; index dùng `/index/{symbol}/data_day`. Host/path xác định từ [const](../.venv/Lib/site-packages/vnstock/explorer/kbs/const.py), không suy chỉ từ nhãn wrapper; upstream tạo dữ liệu phía server chưa xác minh |
| HTTP → parsed JSON | [client](../.venv/Lib/site-packages/vnstock/core/utils/client.py), `send_request` → `send_request_direct` | Direct dùng `requests.get`, check status 200, **return `response.json()`**. Không trả Response/body/headers, không lưu payload; redirects không bị tắt trên đường này |
| Response → normalization | KBS `history`, đoạn chọn `data_day` / dựng DataFrame | Lấy array `json_data['data_day']`, map t/o/h/l/c/v → time/OHLCV, giữ sáu cột mặc định, parse/sort time, cast float/int; equity price chia 1.000, mặc định round 2 decimals; indices/derivatives không chia. `to_df=False` là **`json.dumps(ohlc_data)`**, subset JSON đã parse/re-serialize, không original response bytes. `get_all=True` cũng không phục hồi raw envelope/bytes |

Asset type dựa trên [parser](../.venv/Lib/site-packages/vnstock/core/utils/parser.py), `get_asset_type` (ticker ba ký tự được coi stock); không là authoritative security/lifecycle register. VNINDEX được nhận diện index; normalization equity và index là hai series khác nhau. DataFrame attrs source/symbol/interval/start/end là metadata do adapter gán, không chứng nhận provider authority.

## Đầu vào tối thiểu: có gì, thiếu gì?

| Câu hỏi | Kết luận từ bằng chứng hiện có | Cần bổ sung |
|---|---|---|
| Original response trước normalize? | Response object có điểm kỹ thuật để lưu body trước `response.json`, nhưng đường API đang dùng không expose/archive. **Chưa có capture hợp lệ**, không thể lấy raw bằng `to_df=False` | Transport được review, body bytes trước parse + status/selected headers/request/UTC/hash; phân biệt transport decompression với wire bytes. Chưa xây hoặc sửa transport trong lượt này |
| Unit/raw-adjusted semantics? | Transform equity /1.000 và round(2) **được xác nhận trong code**, không xác nhận raw unit của KBS là VND, volume semantics hay RAW/ADJUSTED. Endpoint history không đưa adjustment parameter/verified method vào batch | Attributable contract unit/security type và method/basis/change semantics; thiếu thì UNKNOWN, không parity/hash substitution |
| Series identity/version? | Pin được requested symbol, path, interval, parser/transform và installed package hash; không có stable security/venue authority hoặc provider method/version | Giữ upstream version NULL/UNKNOWN nếu không được nguồn cung cấp; package 4.0.2 không phải adjustment version. Source/security/venue/unit/basis cần review riêng trước anchor/admission |
| Timestamp/watermark? | Code dùng `t` cho bar time; attrs không có publication time/watermark. Chuẩn hóa bỏ envelope/extra fields, nên **chưa biết server có metadata publication nào**; không kết luận nguồn chắc chắn không cung cấp | Original envelope thực để review schema; observed_at của collector vẫn khác publication time. Một sample không đo stability/delay |
| End=T, thu lặp/revision? | `_format_date_for_api` gửi `sdate/edate` dạng DD-MM-YYYY, `1D` → `day`; request range explicit nên thiết kế end=T khả thi. Chưa đo server có tôn trọng upper bound hoặc trả đầy đủ/repeated data. Normalize chỉ lọc lower bound start | Kiểm tra row date≤T, capture repeated observations và mọi overlap. Rounding 2 decimals có thể che revision nhỏ; so exact source fields trước rounding và giữ cả normalized delta, không đổi detector tolerance `1e-9`. Float→int fallback cho volume có thể truncate giá trị không nguyên: validate source fields trước cast. Hash body khác chưa đủ kết luận price revision |
| Quota/access/retention? | [vnai auth](../.venv/Lib/site-packages/vnai/beam/auth.py), `Authenticator.TIER_LIMITS`: guest 20/min, 1.200/hour, 5.000/day; free 60/min, 3.600/hour, 10.000/day. [quota](../.venv/Lib/site-packages/vnai/beam/quota.py) lấy tier limits, fallback guest. **Đây là library limits, không chứng cứ quota backend KBS/tier thực/remaining shared usage** | Contract đúng access mechanism/endpoint, scope payload retention cá nhân, provider minute/hour/day limits và retry accounting. Không lấy comment Update 60/min làm permission; không gọi direct HTTP để né library licensing |

Package `vnstock==4.0.2` metadata nói license personal/research/non-commercial; `vnai==2.6.0` proprietary. **Package license không cấp quyền lưu/redistribute dữ liệu KBS.** Không có điều khoản payload KBS đủ dùng trong tập bằng chứng đã đọc. [Static review](D4B2_7B_STATIC_SECURITY_REVIEW.md) vẫn PARTIAL: import/call có ancillary network/profile/quota work; telemetry off không khép đường đó. Không tận dụng wrapper retry/proxy/redirect hoặc monkeypatch capture để mở request chưa qualify.

## Pin code và giới hạn

Các hash dưới đây được tính lại từ installed files, không import package. Ba file KBS/client trùng static review cũ; auth là code evidence bổ sung về quota, không snapshot tài khoản/server.

| File trong `.venv/Lib/site-packages` | SHA-256 |
|---|---|
| `vnstock/explorer/kbs/quote.py` | `eddeac3391b31decbb0569c6f9028a61bf6a0437332b1ce492eb1b734b47a088` |
| `vnstock/explorer/kbs/const.py` | `7b43bd33d527f00a466dad28c0c9172c303338d526edae0cc4134a637973cffa` |
| `vnstock/core/utils/client.py` | `da638430637a6f638692bb4cc282fcefdc2c8fdf0746c683f437f4c9514ef23f` |
| `vnai/beam/auth.py` | `e7c141876fc21912cec3f123818346d9e55e3af94674c6ace56f5569430d0337` |

Links installed files chỉ là trace ở máy hiện tại; hashes định danh bytes review, không khẳng định upstream lịch sử hoặc historical availability. Không có response KBS mới hay JSON tái tạo nào được gọi là snapshot original. Packet TPB/CTR, canonical, provenance/receipts và policy hiện có không đổi; operational/research eligibility không được cấp.

Kết luận static ban đầu vẫn giữ: transport original-body capture chưa qualify; unit/basis/publication chưa đủ để admission. Lượt tài liệu bổ sung dưới đây thu hẹp câu hỏi access/retention/quota; không mở pilot, sửa logic hoặc chạy tests/backtest/rehearsal/DB SQL.

## Bổ sung: access/retention/quota — một lượt tài liệu, đã dừng

Đúng đối tượng: `https://kbbuddywts.kbsec.com.vn/iis-server/investment/stocks/{symbol}/data_day` và `/index/{symbol}/data_day`, với `sdate/edate`; không tự coi điều khoản website hay wrapper áp dụng trọn vẹn cho hai endpoint này. Không gọi các endpoint hoặc thử accessibility.

Đã dùng **6 search queries, 8 URL tài liệu/surface chính thức**, không mở rộng sau đó. Queries có domain filter chính thức:

1. `site:vnstocks.com vnstock điều khoản dữ liệu KBS giới hạn API lưu trữ`
2. `site:github.com/thinh-vu/vnstock LICENSE.md KBS data terms`
3. `site:kbsec.com.vn KBS điều khoản sử dụng dữ liệu website`
4. `site:kbsec.com.vn kbbuddy API dữ liệu giới hạn truy cập`
5. `site:kbsec.com.vn "điều khoản" "website"`
6. `site:kbsec.com.vn "sao chép" "dữ liệu"`

Các URL đã kiểm tra: [repo LICENSE](https://github.com/thinh-vu/vnstock/blob/main/LICENSE.md), [docs index](https://vnstocks.com/docs), [Community index](https://vnstocks.com/docs/vnstock), ba tài liệu có snapshot ở bảng sau, [KBS homepage](https://www.kbsec.com.vn/) và [fixed license copy](https://vnstocks.com/api/legal/policy/license). Hai URL cuối không đọc được bằng web tool trong lượt này; không suy nghĩa vụ/quyền từ lỗi retrieval. Không dùng fork vnstock cũ/TCBS, sponsor docs, báo thường niên hoặc terms sản phẩm margin làm quyền của endpoint history.

### Snapshot và phạm vi thời gian

Chỉ report này đổi trong repo. Snapshot hỗ trợ nằm **ngoài repo** ở `C:/Users/hello/.codex/visualizations/2026/10/04/01a10720-75cf-7d50-9214-075861ee51ec/d4-kbs-access-terms-review/`; [collection manifest](C:/Users/hello/.codex/visualizations/2026/10/04/01a10720-75cf-7d50-9214-075861ee51ec/d4-kbs-access-terms-review/document_collection_manifest.json) giữ URL, status, final URL, headers an toàn, size/hash và UTC thu. Đây là exact HTTP response-body bytes trước parse của **tài liệu**, không JSON tái tạo và không wire capture. Các bản `.extracted.txt` chỉ giúp định vị đoạn, không thay snapshot gốc.

| Nguồn chính thức / snapshot | SHA-256 body; thời điểm thu UTC | Version / ngày hiệu lực |
|---|---|---|
| [Vnstock software license](https://vnstocks.com/onboard/giay-phep-su-dung), [HTML](C:/Users/hello/.codex/visualizations/2026/10/04/01a10720-75cf-7d50-9214-075861ee51ec/d4-kbs-access-terms-review/vnstock-license-current.html) | `8deb3af19c6cbbc9f069636a5357c793592d5ec8759d3fca97047bc477c829e4`; 2026-10-05T10:01:14.734955Z | `license-2026.09`, `tos-2026.09`; chưa xác nhận ngày công bố/hiệu lực cụ thể. Mã version không phải ngày 01/09; content hash hiển thị trên trang khác hash HTML ở đây |
| [Vnstock Community introduction](https://vnstocks.com/docs/vnstock/gioi-thieu-vnstock), [HTML](C:/Users/hello/.codex/visualizations/2026/10/04/01a10720-75cf-7d50-9214-075861ee51ec/d4-kbs-access-terms-review/vnstock-community-intro.html) | `f3ead6689b1eddff5a965e8eb3f9603a489d691cb4ab64acea276995011bc1ec`; 2026-10-05T10:01:15.688770Z | Trang ghi v4.0.6, khác installed v4.0.2; không tự hồi tố terms/tier cho installation hiện tại |
| [KBSV Bộ Điều khoản và Điều kiện](https://kbsec.com.vn/pic/general/files/B%E1%BB%99%20%C4%90i%E1%BB%81u%20Kho%E1%BA%A3n%20v%C3%A0%20%C4%90i%E1%BB%81u%20Ki%E1%BB%87n.pdf), [PDF 51 trang](C:/Users/hello/.codex/visualizations/2026/10/04/01a10720-75cf-7d50-9214-075861ee51ec/d4-kbs-access-terms-review/kbs-customer-terms.pdf) | `e4e9cc6616f7f0e1ab1a7d5ca6e7c771bc12a3f683c461c948205e8065747c37`; 2026-10-05T10:01:16.765753Z | §8.9: hiệu lực từ ngày ký hợp đồng, không phải ngày download. Publication date/version chưa xác nhận; HTTP Last-Modified 12/12/2024 và PDF metadata không là ngày hiệu lực của endpoint |

Giữ thêm [installed 4.0.2 LICENSE](C:/Users/hello/.codex/visualizations/2026/10/04/01a10720-75cf-7d50-9214-075861ee51ec/d4-kbs-access-terms-review/vnstock-4.0.2-installed-license.md), SHA-256 `aa2b076b82825c663bb8edde4bf7e1312928f8360106410424ce836d87ebd69d`: bản local license không có revision/effective date đủ để xác định quan hệ với license web mới. Không đổi package hoặc tự chấp thuận license mới. Snapshot hôm nay không chứng minh byte availability lịch sử. Lần tải đầu bị socket sandbox từ chối; một retry được cấp quyền chỉ tải đúng ba URL tài liệu trên. Không tải nội dung account, chạy JavaScript hoặc gửi request market-data.

### Claim mapping: hỗ trợ / hạn chế / chưa xác định

| Mục | Có căn cứ hỗ trợ | Hạn chế rõ / đoạn hỗ trợ | Chưa xác định cho đúng KBS history |
|---|---|---|---|
| License package dùng cá nhân | Installed LICENSE I và web license I hỗ trợ dùng phần mềm cho nghiên cứu cá nhân trong phạm vi được cấp | Web license I/II: “không cấp quyền sử dụng dữ liệu của nguồn bên thứ ba”; III cấm né hạn mức và gây hại hệ thống | Không chứng nhận quyền payload KBS; phiên bản license/tier áp dụng cho installation cần xác định nếu dùng wrapper, không tự chuyển sang license web mới |
| Access tới backend | KBS contract §9.1, PDF page 16–17 (trang in 15–16), mô tả online services đã đăng ký và được KBSV chấp thuận | §11.9, PDF page 19 (trang in 18): “truy cập hoặc sử dụng trái phép” bị hạn chế | Không có mapping từ contract này sang đọc tự động hai `data_day` paths; chưa rõ anonymous/authenticated access được hỗ trợ. Không gọi endpoint readable là permission, cũng không gọi mọi automated read là prohibited |
| Lưu response riêng để kiểm tra/tái hiện | Web license II giao điều kiện lưu trữ dữ liệu nguồn cho chủ nguồn; không chuyển quyền đó từ vnstock | KBS contract §12.8 nói KBSV lưu chứng từ giao dịch khách hàng, không phải quyền người dùng lưu OHLCV payload | Chưa có điều khoản giải quyết private original-body retention/duration cho endpoint này; thiếu điều khoản không là cho phép hoặc cấm |
| Wrapper rate | Community intro, bảng Hạn mức: “Tối đa 60 yêu cầu mỗi phút khi dùng khoá API tạo trên website”; local auth có guest/free limits nêu trên | Có điều kiện API key/tier; không né limiter. Đây là mức wrapper, không quota backend | Chưa xác nhận user tier/remaining shared usage, unit đếm qua hai decorators, hourly/day quota hiện hành của tài khoản. Không đọc key/profile để suy |
| Backend quota | KBS contract §17, PDF page 22 (trang in 21), cho phép KBSV ấn định/thay đổi giới hạn dịch vụ | Giới hạn có thể tồn tại; contract này không cho một con số request cụ thể | Per-IP/account/key limits, minute/hour/day, burst, retry/pagination accounting cho `data_day` chưa xác định; 60 không được chứng nhận |

Không suy unit/basis/completion từ các điều khoản này. Nghĩa vụ online-service trong hợp đồng chỉ được map theo phạm vi đã nêu; chưa khẳng định toàn bộ hợp đồng áp vào read-only public history. Không có câu nào trong lượt tài liệu đủ để cấp access/retention/quota cho pilot, cũng chưa tìm được lệnh cấm đích danh đúng pilot đó.

### Budget 60 phải đếm HTTP attempts, không đếm wrapper calls

- Memo có 4 targets × 2 vòng × 5 phiên = 40 planned reads; vòng thứ ba nâng tối đa 60 slots. **60 là trần đề xuất cho actual KBS market-data attempts**, gồm failed/retry/pagination nếu có; không phải 60 lời gọi `Quote.history` rồi cho retry ngoài trần. Các trần 4/60s và 16/rolling 24h vẫn đề xuất, không được dùng thay backend quota.
- Trên đúng path đã trace, API Quote history có Tenacity `Config.RETRIES=3`; provider history không có pagination loop, một direct GET mỗi lần vào body. 40/60 logical calls có thể tạo tới **120/180 data GETs** nếu mỗi call dùng hết 3 attempts, giả sử không redirect/proxy; đây là upper-bound từ code, không kết quả đo. `BaseAdapter.history` generic retry không nằm trên path override này, nên không nhân thành 3×3. `count_back` trim local, không fetch thêm page.
- `vnai.optimize` retry khi quota verification trước gọi body không tự chứng minh thêm history GET, nhưng quota/license/promo calls là traffic riêng. Redirect/proxy attempts cũng có thể làm logical call sinh thêm HTTP; không được bỏ khỏi accounting. Wrapper quota counters và backend HTTP accounting là hai lớp khác nhau, cần xác nhận từ đơn vị quản lý.
- Vì chưa có transport/counter được review bảo đảm actual-attempt cap và original-body capture, **không thể duyệt budget bằng phép so 4 < 20/60**. Không chạy một request thử để suy quota, stability hoặc permission. Pilot chưa khởi động; mọi số actual pilot attempts hiện là 0.

### Câu hỏi đã soạn — KHÔNG GỬI

Đơn vị phù hợp: **KBSV, bộ phận KB Buddy/CSKH, đề nghị chuyển nhóm quản lý backend hoặc cấp quyền dữ liệu**. Vnstock chỉ có thể làm rõ wrapper license/tier; không mặc nhiên đại diện cấp quyền KBS. Không đoán email người nhận và không liên hệ ai trong lượt này.

> Tôi nghiên cứu cá nhân, không phân phối dữ liệu. KBSV có cho phép đọc tự động `https://kbbuddywts.kbsec.com.vn/iis-server/investment/stocks/{symbol}/data_day` và `/index/{symbol}/data_day`, rồi lưu nguyên response cục bộ để kiểm tra/tái hiện không? Cơ chế truy cập được hỗ trợ là gì? Pilot gồm TPB/VHM/CTR và VNINDEX trong 5 phiên, tối đa 60 HTTP attempts kể cả lỗi/retry/pagination, trần 4/phút và 16/24 giờ. Xin xác nhận quota/backend accounting và điều kiện/thời hạn lưu, hoặc dẫn tài liệu áp dụng cho hai endpoint này.

**Kết luận cập nhật:** đã biết phạm vi phần mềm cá nhân và wrapper-rate có điều kiện; đã thấy hạn chế chung về truy cập trái phép và giới hạn dịch vụ KBS. **Chưa biết** permission cho automated history read, private raw retention và quota backend cụ thể. Pilot đang chờ trả lời KBSV hoặc tài liệu chính thức áp dụng đúng endpoint giải quyết ba mục đó; sau đó vẫn phải qualify transport/counter/body capture trước khi thu. Thiếu câu trả lời giữ UNRESOLVED, không diễn giải thành cấm hay cho phép. Unit/basis/publication và operational confidence/research eligibility vẫn riêng, không được nâng. Chỉ sửa report này; memo, packet, policy/code/DB và 18 artifacts được giữ nguyên; không tests, prototype, provider data request, stage/commit/push.
