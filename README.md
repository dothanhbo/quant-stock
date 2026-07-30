# Quant Stock – Strategy Phase 1

Bot định lượng quét VN100, phân loại trạng thái thị trường, chấm điểm cổ phiếu, xếp hạng tín hiệu và gửi Telegram. Phase 1 tập trung hoàn thiện **strategy/scanner**; các thư mục backtest được giữ lại để phát triển Phase 2.

## Tính năng Phase 1

- Market Regime: `BULL`, `SIDEWAY`, `BEAR`, `UNKNOWN`.
- Cấu hình ngưỡng bằng `config/strategy.yaml`, không cần sửa source.
- EMA10/20/50, RSI, ADX, ATR, Volume Ratio, breakout và price action.
- Relative Strength 20 phiên so với VNINDEX.
- Scoring 0–100, Top Ranking và Watchlist kèm điều kiện còn thiếu.
- Stop Loss theo ATR/EMA20 và Take Profit theo RR của từng regime.
- Dashboard cuối phiên và thống kê điều kiện bị loại độc lập.
- Cache chỉ báo trong RAM để tránh tính lại cùng dữ liệu trong một tiến trình.
- Unit test cho ATR, scoring, cấu hình regime và cache.
- Chống trùng tín hiệu và gửi báo cáo Telegram.

## 1. Cài đặt

Yêu cầu Python 3.10–3.13 được khuyến nghị.

```powershell
cd quant-stock
py -m venv venv
.\venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
pip install -r requirements.txt
```

Nếu PowerShell chặn activate:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 2. Cấu hình Strategy

Sửa file:

```text
config/strategy.yaml
```

Ví dụ:

```yaml
regimes:
  BULL:
    min_score: 60
    min_adx: 18
    min_volume_ratio: 1.0
  SIDEWAY:
    min_score: 66
  BEAR:
    min_score: 74
```

Sau khi lưu file, lần chạy mới sẽ tự đọc cấu hình. Không cần sửa `scanner.py`.

## 3. Cấu hình Telegram

Tạo file `.env` ở thư mục gốc:

```env
TELEGRAM_TOKEN=your_bot_token
CHAT_ID=your_chat_id
```

Không commit file `.env` lên GitHub.

## 4. Khởi tạo và cập nhật dữ liệu

```powershell
py -m scripts.init_db
py -m scripts.update_data
py -m scripts.update_index
```

Kiểm tra database:

```powershell
py -m scripts.check_db
```

## 5. Chạy Scanner

```powershell
py -m strategy.scanner
```

Kết quả gồm:

- Market regime và ngưỡng đang áp dụng.
- Top tín hiệu đạt toàn bộ hard filters.
- Watchlist gần đạt, ví dụ `68/66 | thiếu: volume`.
- Dashboard cuối phiên.
- Telegram report nếu `.env` hợp lệ.

## 6. Chạy Unit Test

```powershell
pytest -q
```

Kiểm tra cú pháp toàn dự án:

```powershell
py -m compileall -q .
```

## 7. Cache chỉ báo

`strategy/cache.py` lưu kết quả indicator theo mã và fingerprint dữ liệu. Cache có ích khi cùng dữ liệu được đánh giá nhiều lần trong một tiến trình, đặc biệt khi chuẩn bị backtest hoặc gọi scanner lặp lại. Kích thước cache chỉnh tại:

```yaml
common:
  indicator_cache_size: 256
```

Cache là bộ nhớ tạm và tự mất khi chương trình kết thúc.

## 8. Cấu trúc chính

```text
quant-stock/
├── config/
│   ├── strategy.yaml
│   └── strategy_loader.py
├── core/
├── services/
├── strategy/
│   ├── cache.py
│   ├── indicators.py
│   ├── market_regime.py
│   ├── relative_strength.py
│   └── scanner.py
├── scripts/
├── tests/
├── market.db
├── requirements.txt
└── README.md
```

## 9. Backtest – Phase 2

Các lệnh hiện có để nghiên cứu tiếp:

```powershell
py -m backtesting.engine
py -m backtesting.multi_symbol
```

Trước khi dùng kết quả để giao dịch thật, cần backtest, kiểm tra phí giao dịch, trượt giá, thanh khoản và out-of-sample. Scanner là công cụ hỗ trợ nghiên cứu, không phải cam kết lợi nhuận.

## Kiến trúc sau refactor

```text
quant-stock/
├── config/                 # YAML và loader cấu hình
├── core/                   # Database, logging, lưu tín hiệu
├── strategy/
│   ├── scanner.py          # Điều phối luồng quét
│   ├── filters.py          # Điều kiện bắt buộc
│   ├── scoring.py          # Chấm điểm 0–100
│   ├── watchlist.py        # PASSED/WATCHLIST/REJECTED
│   ├── indicators.py       # Chỉ báo kỹ thuật
│   ├── cache.py            # Cache indicator
│   ├── market_regime.py    # BULL/SIDEWAY/BEAR
│   └── relative_strength.py
├── risk/
│   └── levels.py           # Entry, stop loss, take profit
├── reporting/
│   └── dashboard.py        # Kết quả console và dashboard cuối phiên
├── services/
│   ├── market_data.py
│   └── telegram.py
├── backtesting/            # Nền tảng cho Phase 2
├── scripts/                # Update/init dữ liệu
├── tests/                  # Unit tests
└── app/                    # Entry points/scheduler
```

`strategy/scanner.py` hiện chỉ điều phối. Logic scoring, filter, watchlist, risk và reporting đã được tách riêng để Phase 2 có thể tái sử dụng mà không sao chép logic.

### Kiểm tra sau khi clone

```powershell
pip install -r requirements.txt
pytest -q
py -m strategy.scanner
```

Bộ test refactor hiện có 12 test cho ATR, cache, scoring, market config, filter, watchlist và risk.
