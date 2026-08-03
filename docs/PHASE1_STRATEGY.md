# Phase 1 – Strategy hoàn thiện

Phiên bản này đã hoàn thiện các thành phần chính của scanner:

- Market Regime động: BULL / SIDEWAY / BEAR / UNKNOWN.
- Ngưỡng score, ADX, volume, relative strength và RR thay đổi theo regime.
- Relative Strength 20 phiên so với VNINDEX, hỗ trợ `end_date` để backtest không nhìn trước dữ liệu.
- Score 100 điểm gồm trend, volume, RSI, ADX, breakout, price action và relative strength.
- Stop loss theo ATR kết hợp EMA20; take profit theo RR của từng regime.
- Xếp hạng tín hiệu theo score, relative strength, volume và ADX.
- Watchlist cho mã gần đạt điều kiện.
- Thống kê từng điều kiện fail độc lập.
- Telegram hiển thị regime, Top tín hiệu và watchlist.
- `check_signal()` giữ tương thích backtest: trả về `dict` hoặc `None`.

## Chạy scanner

```powershell
py -m strategy.scanner
```

## Cấu hình Telegram

Tạo file `.env` ở thư mục gốc:

```env
TELEGRAM_TOKEN=your_bot_token
CHAT_ID=your_chat_id
```

Không ghi token trực tiếp vào source code.

## File chính đã thay đổi

- `strategy/scanner.py`
- `strategy/market_regime.py`
- `strategy/relative_strength.py`
- `services/telegram.py`
