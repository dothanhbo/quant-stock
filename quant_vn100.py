import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from vnstock.api.quote import Quote

# ==========================================
# 1. CẤU HÌNH BOT TELEGRAM
# ==========================================
TELEGRAM_TOKEN = "8860199022:AAHNtR2Xd5eekkzRvG_ILrslvrc4pKNwd2I"
CHAT_ID = "5137019839e"

# ==========================================
# 2. THAM SỐ LỌC KỸ THUẬT (CÓ THỂ ĐIỀU CHỈNH)
# ==========================================
VOL_FACTOR = 1.2     # Volume dự kiến gấp >= 1.2 lần MA20
RSI_MIN = 45         # RSI từ 45 trở lên (xu hướng khỏe)
RSI_MAX = 70         # RSI dưới 70 (tránh mua đuổi quá mua)

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

def calculate_rsi(series, period=14):
    """Tính RSI bằng Pandas thuần"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_market_progress():
    """Tính tỷ lệ thời gian phiên giao dịch đã trôi qua"""
    now = datetime.now()
    market_start = now.replace(hour=9, minute=15, second=0)
    market_end = now.replace(hour=14, minute=30, second=0)
    
    if now <= market_start:
        return 0.1
    if now >= market_end:
        return 1.0
        
    elapsed = (now - market_start).total_seconds()
    if now.hour >= 13:
        elapsed -= 5400
        
    return max(0.1, min(1.0, elapsed / 15300))

def scan_quant_signals():
    today_str = datetime.now().strftime("%Y-%m-%d")
    start_str = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    progress = get_market_progress()
    tickers = list(set(VN100_LIST))
    
    print(f"🔍 Bắt đầu quét {len(tickers)} cổ phiếu VN100...")
    print(f"⏱️ Tiến độ phiên giao dịch: {round(progress*100, 1)}%\n")
    
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
            
            # Dự báo Volume cả ngày dựa trên thời gian thực
            est_vol = latest['volume'] / progress if progress < 1.0 else latest['volume']

            # Các điều kiện lọc kỹ thuật
            cond_ma = latest['close'] > latest['MA10']  # Giá nằm trên MA10
            cond_vol = est_vol >= (VOL_FACTOR * latest['Vol_MA20'])  # Đột biến Volume
            cond_rsi = RSI_MIN <= latest['RSI'] <= RSI_MAX  # RSI hợp lý

            if cond_ma and cond_vol and cond_rsi:
                count_matches += 1
                price_vnd = latest['close'] * 1000
                vol_ratio = round(est_vol / latest['Vol_MA20'], 1)
                
                msg = (
                    f"🚀 *[TÍN HIỆU TĂNG TRƯỞNG VN100]* 🚀\n\n"
                    f"📌 *Mã cổ phiếu:* `{ticker}`\n"
                    f"📈 *Giá hiện tại:* {price_vnd:,.0f} VNĐ\n"
                    f"📊 *Vol ước tính:* {int(est_vol):,} CP (Gấp *{vol_ratio}x* MA20)\n"
                    f"🎯 *Chỉ số RSI:* {latest['RSI']:.1f}\n"
                    f"⚡ *Đánh giá:* Vượt MA10 + Đột biến Vol + RSI trong vùng mua đẹp!"
                )
                
                print(f"✅ [{i+1}/{len(tickers)}] TÌM THẤY MÃ: {ticker} | Giá: {price_vnd:,.0f} | RSI: {latest['RSI']:.1f}")
                send_telegram(msg)

            time.sleep(3.0)

        except Exception as e:
            continue

    print(f"\n🎉 Quét xong! Tìm thấy tổng cộng {count_matches} mã thỏa mãn bộ lọc.")

if __name__ == "__main__":
    scan_quant_signals()