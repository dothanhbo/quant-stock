# D4 — kế hoạch pilot EOD nhỏ và quyết định nguồn

Ngày sửa: 2026-10-05, Asia/Bangkok. HEAD thực tế: `ec3566ab4db4d28667c7fc3bbb6d3de9eca342c3`, branch `main`; tracked tree/index sạch. Memo cũ, packet TPB và 18 artifacts đang có chưa commit. **PROPOSAL_ONLY; chưa duyệt operational confidence, UNKNOWN basis hoặc research eligibility.**

**Chọn một pilot KBS observation-only trong 5 phiên mục tiêu, chỉ lưu files ở kho shadow riêng. Chưa đủ cơ sở mở network pilot.** [Qualification KBS](D4_KBS_PILOT_SOURCE_QUALIFICATION.md) xác nhận endpoint và phép normalize qua code, nhưng access/retention, quota KBS và transport lưu body trước parse còn chưa qualify. Không gọi `Quote.history()` đang bị [static review](D4B2_7B_STATIC_SECURITY_REVIEW.md) chặn; không tự xây collector trong lượt này.

## 1. Phạm vi, nhóm mã và ngân sách

Nhóm cố định: **TPB, VHM, CTR**, cộng **VNINDEX** làm corroborator cùng provider, không phải chứng cứ độc lập hay authority lịch sàn. Chọn ba mã vì đã có rủi ro boundary/basis/lifecycle được ghi nhận, không chọn để dễ PASS. HOSE là venue dự kiến phải review cho từng security/date; không suy TPB/HSX → HOSE từ filename hoặc ticker. Không thay mã thiếu dữ liệu bằng mã khác trong pilot.

- 5 phiên mục tiêu T khai báo trước bằng calendar snapshot; chưa chốt ngày bắt đầu. Thiếu chứng cứ phiên thực → SESSION_UNKNOWN, không đếm là phiên completed đã qualify. Kết thúc sau cửa sổ 5 phiên mục tiêu, không kéo dài để đạt PASS.
- Mỗi request lấy history `1D`, **start=T−14 ngày lịch, end=T**. Range ngắn để thấy overlap; không watch 120 ngày, cohort toàn universe, bulk fetch hay bootstrap lịch sử cũ.
- Lịch đo: **18:30 ngày T, 08:00 ngày lịch T+1**; thêm tối đa một vòng **09:00** nếu thiếu/lỗi/changing. **Cutoff 09:30 T+1**. Đây là lịch thử, không phải delay đã chứng minh. Hai lần giống nhau không chứng minh finality. Không đặt 60 phút thành completion rule đã duyệt.
- 4 targets × 2 vòng = **8 GET/phiên**; tối đa 3 vòng = **12 attempts/phiên, 60 attempts/5 phiên**. Các attempts lỗi/retry cũng tính; không retry ngầm, redirect hoặc ancillary network. Các request tuần tự, cách nhau ít nhất 20 giây, trần **4 attempts/60 giây và 16 attempts/rolling 24 giờ** để tính cả cửa sổ T/T+1 giao nhau. Một vòng kết thúc trước cutoff; không phát request mới sau cutoff.
- Đây là **budget đề xuất, chưa là quota được cấp**. Code package có guest 20/min, 1.200/hour, 5.000/day và free 60/min; tier thực và quota backend KBS chưa xác nhận. Comment Update “60/min” không đủ. Chỉ mở pilot khi hợp đồng quota/access cho đúng cơ chế thu cho phép budget này, tính cả usage khác; thấp hơn thì cần sửa/duyệt budget trước. 429/authorization failure → dừng lượt và giữ review, không lách tier/quota.

Pilot không gọi Update/Daily, không có canonical connection, không gọi writer/admission, không scanner, paper/Forward/Telegram. CafeF đã lưu chỉ đối chiếu thủ công khi có đúng ngày; không tạo thêm vòng thu CafeF. Thiếu CafeF ghi “chưa đối chiếu”, không giả upstream độc lập.

## 2. Observation và thời gian được giữ thế nào?

Kho riêng có manifest cho run/target/attempt, original response body **trước JSON parse**, SHA-256, status, header an toàn, request URL/range, redirect/content-encoding metadata, parser/package/code hash và normalization version. Body bytes được lưu đúng cách transport thu; nếu transport đã decompress thì ghi rõ, không gọi là wire capture. Không lưu token/cookie/key. Response lỗi cũng giữ bằng chứng được phép lưu; replay file không là network observation mới.

- `session_date`: ngày row do nguồn khai báo, đối chiếu riêng calendar/lifecycle.
- `observed_at`: UTC thực nhận response; giữ cả request start/end. Không phải publication time của nguồn; field đó/watermark để NULL khi không có.
- `checks_completed_at`, `reviewed_at`: thời điểm thực kiểm tra và người review.
- `operational_usable_at`: **NULL trong pilot**. Nếu tương lai được duyệt, không sớm hơn response cuối cần thiết, hoàn tất checks và quyết định review/admission. Không backdate về T hoặc lần thu đầu.

Cutoff T+1 có thể sau giờ mở cửa. Giá của T không có nghĩa tín hiệu đã sẵn trước mở cửa T+1; không mô phỏng khớp trước `operational_usable_at`. Pilot không thay protocol paper/Forward. Nó chỉ đo first-seen, thay đổi giữa các lần thu, coverage và thời gian hoàn tất review; không đo được publication time chính xác nếu nguồn không công bố.

## 3. Bootstrap anchor — thu trước, review sau

**Không cần anchor để bắt đầu lưu observations.** Điều kiện bắt đầu thu là access/retention/quota và transport observation-only được qualify, danh sách targets/range/calendar mục tiêu được đóng băng; calendar/lifecycle/unit/basis chưa đủ vẫn lưu UNKNOWN để điều tra, không admission. Bootstrap không dùng giá canonical làm điểm xuất phát.

1. Từ phiên đầu, tích lũy bytes/rows vào **file staging riêng**; mỗi generation giữ nguồn, exact claims và hash. Không gán provenance/source/basis mới cho legacy rows.
2. Đề xuất anchor-set khi có **hai phiên liền kề của cùng security/venue/series**: mỗi phiên có ít nhất hai observations thật khác thời điểm; history request ở phiên sau lấy lại cả hai, so mọi overlap và giữ exact old/new. Sớm nhất có thể review sau observations của phiên thứ ba; không bắt buộc đủ ngay ngày đầu.
3. Chỉ review anchor là hợp lệ khi identity/lifecycle/calendar cover đúng hai ngày, unit/normalization rõ, hash/row validation đạt, không incident/unresolved boundary/CA, không revision/missing anchor và có đủ publication evidence theo policy áp dụng. Basis UNKNOWN/operational confidence chỉ dùng nếu **sau này được duyệt rõ**, không tự phê duyệt trong pilot.
4. Review tạo artifact anchor-set/version/`reviewed_at` trong kho riêng, không INSERT vào canonical hay chuyển BOOTSTRAP_PENDING thành acceptance. Khởi tạo một series shadow mới từ anchors sau review cần quyết định bootstrap riêng; admission hiện có với empty history vẫn BOOTSTRAP_PENDING. Phiên tiếp theo mới có thể dùng hai anchors đã review để xét append ở bước sau. Nếu sau 5 phiên vẫn thiếu bằng chứng → ANCHORS_UNRESOLVED, pilot vẫn kết thúc với kết quả hữu ích.

Revision trong pilot là thay đổi **source observation**, không tuyên bố đã revise canonical. Giữ toàn observation, exact old/new, claims/refs; không bỏ phần sai để cứu ngày mới. Không overwrite file/generation hay tự promotion.

## 4. Gate và kết quả pilot

| Gate → rủi ro | Bằng chứng khả thi / điều còn thiếu | Quyết định trong pilot |
|---|---|---|
| Access/transport/quota → thu ngoài điều kiện nguồn | Trace có endpoint KBS; package có tier limits, không là quota/retention KBS | Thiết yếu trước network; còn BLOCKED |
| Snapshot/version → mất bytes, nhầm normalize với raw | Existing helper trả parsed JSON; `to_df=False` là JSON tái tạo | Cần body trước parse; hash chỉ chứng minh integrity, không approval |
| Calendar/lifecycle → non-session, chuyển sàn/đình chỉ | HOSE annual baseline còn thiếu; CTR packet chỉ phủ định đúng ngày lịch sử, TPB chưa qualify | Review register/interval và ngoại lệ có log; thiếu → UNKNOWN, không nhận giá. Không tìm riêng văn bản đóng phiên 02/10 |
| Publication/freshness/coverage → partial/stale | Chưa watermark/delay; khả năng `end=T` chỉ được thấy trong code | Đo row T, coverage, timestamps, changing/stability; không gán OPEN_COMPLETED/finality |
| OHLCV → malformed/duplicates | Validate source fields trước rounding/cast: finite/positive OHLC, low ≤ open/close ≤ high, volume nguyên không âm, unique key/date ≤ T | Phân loại invalid/hold; không forward-fill. Zero volume/row lặp cần ngữ cảnh, không tự coi sai |
| Unit/basis/series → trộn scale/adjustment | Code chia equity price /1.000, round mặc định 2; chưa là xác nhận semantics KBS | Giữ unit/basis/method UNKNOWN khi thiếu nguồn; pin endpoint/security/asset class/parser transform. Raw/adjusted không suy từ parity |
| Overlap/revision → đổi lịch sử | Có detector/staging D4B1/D4B2; pilot so snapshots mới/range ngắn | Review anchors theo mục 3; không legacy provenance, không watch toàn universe |
| Operational/research → auto promotion/look-ahead | Clone checkpoint có 59 scope cases + 25 entrypoint cases; nguồn thật chưa qualify | Không chạy writer/tests lại; `operational_eligible=false`, `research_eligible=false`, availability lịch sử UNVERIFIED |

Trạng thái file: OBSERVED_UNREVIEWED → REVIEW_REQUIRED / INVALID / ANCHOR_SET_PROPOSED / REVIEWED_ANCHOR_ONLY. **Không trạng thái nào cấp dữ liệu vận hành hoặc thay receipt hiện có.** Thiếu row/notice/metadata ghi đúng reason, không gọi ngày đó SUCCESS.

**Cờ 10% vẫn chỉ PROPOSAL_ONLY:** `abs(close_T / close_prev − 1) >= 0.10`, chỉ cho cặp phiên liền kề TPB/VHM/CTR thuộc venue HOSE đã review, cùng equity series/unit/normalization; không áp vào VNINDEX points hay ghép CafeF/KBS. Không anchor/unit/session hợp lệ → N/A, không “0 cảnh báo”. Basis UNKNOWN không cho diễn giải đây là shareholder return hay CA; cờ chỉ ưu tiên review. Không đổi D4A 35% hoặc tolerance `1e-9`.

Đo **alert rate = số cặp (symbol,prev,T) duy nhất bị cờ / số cặp đủ điều kiện so sánh**; không đếm lặp theo số lần thu. Với 3 mã/5 phiên mới, tối đa 12 cặp nội-pilot; báo riêng mẫu thiếu/N/A, số flags được giải thích bằng nguồn và số chưa giải quyết. Mẫu nhỏ không đủ calibrate ngưỡng hay xác nhận giá đúng/sai.

## 5. Policy và release sau pilot

Mọi ý tưởng operational confidence từ calendar + exception monitoring + observed activity, hoặc basis UNKNOWN có pin riêng, vẫn **PROPOSAL_ONLY**. Không ép nguồn thiếu thành VERIFIED. Không coi năm phiên ổn định là completion SLA hay whole-history certification. A — operational dưới rủi ro được duyệt — vẫn khác B — alpha cần basis/CA/session/incident exclusion/lineage toàn cửa sổ và vintage/as-of. Packet TPB hiện tại không được retro-admit.

Release barrier tương lai thu hẹp vào **entrypoints và vận hành được hỗ trợ**: Update/Daily, Backfill/bootstrap, direct save, D4B1, legacy shadow/manual apply, cleanup/quarantine phải route admission/staging hoặc bị từ chối; chỉ controlled writer có DB write permission, không chạy legacy tool cùng target, rollback build giữ no-write legacy. Chưa wiring hoặc chứng minh barrier này. Pilot files không phụ thuộc hoàn thành barrier production.

Prototype bảo vệ ordinary DML trên factory connection khi authorizer còn nguyên; **không chống caller đặc quyền dùng base API gỡ guard, process owner sửa private state/schema hay filesystem owner**. Các escape đó ngoài threat model vận hành được đề xuất, không phải yêu cầu xây anti-tamper hoàn chỉnh trong cùng process. Không gọi capability bất khả phá; thay phạm vi release cần review riêng, không tự đánh dấu các gate cũ trong [migration plan](D4_MARKET_WRITER_MIGRATION_PLAN.md) đã đạt.

## Kết luận và đúng một bước tiếp theo

**Chưa đủ cơ sở mở KBS read-only pilot.** Thiếu bằng chứng access/retention/quota đúng endpoint và transport được review có original-body capture; unit/basis/publication vẫn chưa qualify nhưng có thể giữ UNKNOWN trong observations sau khi điều kiện thu hợp lệ.

**Bước tiếp theo duy nhất:** bổ sung một **KBS access/retention/quota contract có nguồn kiểm chứng**, cho đúng host/path, cơ chế truy cập được phép, lưu payload riêng cho nghiên cứu cá nhân, limits theo minute/hour/day và cách tính retry/usage chung. Hoàn thành khi có URL hoặc văn bản nguồn, snapshot/hash/ngày hiệu lực và review chỉ ra budget 4 targets/60 attempts có được phép không; package license/comment quota không thay hợp đồng payload KBS. Chưa có contract thì zero requests. Khi đủ mới xét qualification transport riêng; không mặc nhiên bắt đầu pilot, không tự nâng eligibility.

Căn cứ: [policy](DATA_INTEGRITY_POLICY_DECISION.md), [shadow plan](D4B2_SHADOW_ACTIVATION_PLAN.md), [migration](D4_MARKET_WRITER_MIGRATION_PLAN.md), [checkpoint](D4_SHADOW_PROTOTYPE_CHECKPOINT_REVIEW.md), [TPB](eod_source_evidence/TPB_HSX_2026-10-02/README.md) và qualification KBS mới. Các snapshot cũ nói thiếu symbol-session/attributed D4B1 blocking đã được code checkpoint bổ sung; nguồn thật vẫn BLOCKED. Lượt này chỉ sửa memo và thêm qualification report, kiểm tra nội dung/link/whitespace/hash preservation; không tests/backtest/rehearsal/network, không code/policy/schedule/DB/pipeline changes, không stage/commit/push.
