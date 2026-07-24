import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from vnstock import Stock

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

VN100_LIST = [
    "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
    "MBB", "MSN", "MWG", "PLX", "POW", "SAB", "SSB", "SSI", "STB", "TCB",
    "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VRE", "AAA",
    "AGG", "ASM", "BCG", "BFC", "BMP", "CII", "CTD", "DBC", "DCM", "DIG",
    "DPM", "DXG", "EIB", "EVF", "FRT", "GEX", "HDG", "HHV", "HSG", "KBC",
    "KDC", "KDH", "LPB", "MSB", "NLG", "NT2", "NVL", "OCB", "PAN", "PC1",
    "PDR", "PHR", "PVD", "PVT", "REE", "SBT", "SJS", "SZC", "TCH", "VCI",
    "VGC", "VHC", "VIX", "VPI", "VOS", "VSC"
]

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

def calculate_rsi_vectorized(df, period=14):
    """Tính RSI cho từng mã cổ phiếu trong DataFrame chung bằng GroupBy"""
    delta = df.groupby('ticker')['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.groupby(df['ticker']).rolling(window=period).mean().reset_index(0, drop=True)
    avg_loss = loss.groupby(df['ticker']).rolling(window=period).mean().reset_index(0, drop=True)
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def scan_quant_signals_fast():
    start_time = time.time()
    today_str = datetime.now().strftime("%Y-%m-%d")
    start_str = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    print(f"🚀 Bắt đầu quét hàng loạt {len(VN100_LIST)} mã VN100...")

    # 1. Tải toàn bộ dữ liệu lịch sử của VN100 trong 1 Request duy nhất
    stock = Stock(source='VCI')
    df = stock.quote.history(symbol=VN100_LIST, start=start_str, end=today_str, interval='1D')

    if df is None or df.empty:
        print("❌ Không lấy được dữ liệu từ API.")
        return

    # Chuẩn hóa tên cột
    df.columns = df.columns.str.lower()
    df = df.sort_values(by=['ticker', 'time'])

    # 2. Tính toán kỹ thuật hàng loạt bằng Vectorization (Cực nhanh)
    df['ma10'] = df.groupby('ticker')['close'].transform(lambda x: x.rolling(10).mean())
    df['ma20'] = df.groupby('ticker')['close'].transform(lambda x: x.rolling(20).mean())
    df['vol_ma20'] = df.groupby('ticker')['volume'].transform(lambda x: x.rolling(20).mean())
    df['rsi'] = calculate_rsi_vectorized(df, period=14)

    # 3. Lấy chỉ phiên mới nhất của từng mã
    latest_df = df.groupby('ticker').last().reset_index()

    # 4. Áp bộ lọc logic
    cond_ma = latest_df['close'] > latest_df['ma10']
    cond_vol = latest_df['volume'] >= (VOL_FACTOR * latest_df['vol_ma20'])
    cond_rsi = (latest_df['rsi'] >= RSI_MIN) & (latest_df['rsi'] <= RSI_MAX)

    matches = latest_df[cond_ma & cond_vol & cond_rsi]

    # 5. Thông báo kết quả
    print(f"✅ Quét xong trong {round(time.time() - start_time, 2)} giây!")
    print(f"🎯 Tìm thấy {len(matches)} mã thỏa mãn.\n")

    for _, row in matches.iterrows():
        ticker = row['ticker']
        price_vnd = row['close'] * 1000
        actual_vol = row['volume']
        vol_ratio = round(actual_vol / row['vol_ma20'], 1)
        rsi_val = row['rsi']

        msg = (
            f"🚀 *[TÍN HIỆU TĂNG TRƯỞNG VN100]* 🚀\n\n"
            f"📌 *Mã cổ phiếu:* `{ticker}`\n"
            f"📈 *Giá đóng cửa:* {price_vnd:,.0f} VNĐ\n"
            f"📊 *Vol thực tế:* {int(actual_vol):,} CP (Gấp *{vol_ratio}x* MA20)\n"
            f"🎯 *Chỉ số RSI:* {rsi_val:.1f}\n"
            f"⚡ *Đánh giá:* Vượt MA10 + Đột biến Vol + RSI trong vùng mua đẹp!"
        )

        print(f"-> {ticker} | Giá: {price_vnd:,.0f} | RSI: {rsi_val:.1f}")
        send_telegram(msg)
        time.sleep(0.5)

if __name__ == "__main__":
    scan_quant_signals_fast()
