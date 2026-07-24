import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from vnstock import Vnstock
from vnstock.api.quote import Quote

# ==========================================
# 1. CẤU HÌNH BOT TELEGRAM
# ==========================================
TELEGRAM_TOKEN = "8860199022:AAHNtR2Xd5eekkzRvG_ILrslvrc4pKNwd2I"
CHAT_ID = "5137019839e"

# ==========================================
# 2. THAM SỐ LỌC KỸ THUẬT
# ==========================================
VOL_FACTOR = 1.2
RSI_MIN = 45
RSI_MAX = 70

# ==========================================
# 3. CÁC HÀM BỔ TRỢ
# ==========================================
def get_vn100_symbols():
    """Lấy danh sách mã VN100 chuẩn từ class Vnstock"""
    stock = Vnstock()
    df_symbols = stock.stock(symbol='ACB', source='VCI').listing.symbols_by_group('VN100')
    
    # Kiểm tra dữ liệu trả về dạng Series hay DataFrame để lấy danh sách mã
    if isinstance(df_symbols, pd.DataFrame):
        return df_symbols['ticker'].tolist()
    return list(df_symbols)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ==========================================
# 4. HÀM SCAN CHÍNH
# ==========================================
def scan_quant_signals():
    today_str = datetime.now().strftime("%Y-%m-%d")
    start_str = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    # Lấy danh sách VN100
    tickers = get_vn100_symbols()

    print(f"🔍 Bắt đầu quét {len(tickers)} cổ phiếu VN100...")
    
    count_matches = 0

    for i, ticker in enumerate(tickers):
        try:
            q = Quote(symbol=ticker, source='VCI')
            df = q.history(start=start_str, end=today_str, interval='1D')
            
            if df is None or df.empty or len(df) < 25:
                continue

            # Tính toán chỉ báo
            df['MA10'] = df['close'].rolling(window=10).mean()
            df['MA20'] = df['close'].rolling(window=20).mean()
            df['Vol_MA20'] = df['volume'].rolling(window=20).mean()
            df['RSI'] = calculate_rsi(df['close'], period=14)

            latest = df.iloc[-1]
            actual_vol = latest['volume']

            # Điều kiện lọc kỹ thuật
            cond_ma = latest['close'] > latest['MA10']
            cond_vol = actual_vol >= (VOL_FACTOR * latest['Vol_MA20'])
            cond_rsi = RSI_MIN <= latest['RSI'] <= RSI_MAX

            if cond_ma and cond_vol and cond_rsi:
                count_matches += 1
                price_vnd = latest['close'] * 1000
                vol_ratio = round(actual_vol / latest['Vol_MA20'], 1)
                
                msg = (
                    f"🚀 *[TÍN HIỆU TĂNG TRƯỞNG VN100]*\n\n"
                    f"📌 *Mã cổ phiếu:* `{ticker}`\n"
                    f"📈 *Giá đóng cửa:* {price_vnd:,.0f} VNĐ\n"
                    f"📊 *Vol thực tế:* {int(actual_vol):,} CP (Gấp *{vol_ratio}x* MA20)\n"
                    f"🎯 *Chỉ số RSI:* {latest['RSI']:.1f}\n"
                    f"⚡ *Đánh giá:* Vượt MA10 + Đột biến Vol + RSI trong vùng mua đẹp!"
                )
                
                print(f"✅ [{i+1}/{len(tickers)}] TÌM THẤY MÃ: {ticker} | Giá: {price_vnd:,.0f} | RSI: {latest['RSI']:.1f}")
                send_telegram(msg)

            time.sleep(3)

        except Exception as e:
            continue

    print(f"\n🎉 Quét xong! Tìm thấy tổng cộng {count_matches} mã thỏa mãn bộ lọc.")

if __name__ == "__main__":
    scan_quant_signals()
