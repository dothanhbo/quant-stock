# Quant Stock

Bot quét tín hiệu và backtest cổ phiếu Việt Nam.

## Cấu trúc

- `app/`: điều phối ứng dụng
- `config/`: cấu hình và danh sách mã
- `core/`: database, logging và lưu tín hiệu
- `strategy/`: indicator và logic tạo tín hiệu
- `services/`: dữ liệu thị trường và Telegram
- `backtesting/`: engine, backtest đa mã và optimizer
- `analysis/`: báo cáo, phân tích và so sánh bộ lọc
- `scripts/`: tác vụ cập nhật dữ liệu và tiện ích database
- `tests/`: kiểm thử

## Chạy bot

```powershell
py main.py
```

## Backtest và tối ưu

```powershell
py -m backtesting.engine
py -m backtesting.multi_symbol
py -m backtesting.optimize_exit_fast
```

## Kiểm tra cú pháp toàn dự án

```powershell
py -m compileall -q .
```
