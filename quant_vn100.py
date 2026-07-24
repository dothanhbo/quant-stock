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
VOL_FACTOR = 1.2     # Volume thực tế gấp >= 1.2 lần MA20
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

def scan_quant_signals():
    today_str = datetime.now().strftime("%Y-%m-%d")
    start_str = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    tickers = list(set(VN100_LIST))
    
    print(f"🔍 Bắt đầu quét {len(tickers)} cổ phiếu VN100 (Dữ liệu sau phiên)...")
    
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
            actual_vol = latest['volume']  # Sử dụng khối lượng thực tế khớp lệnh trong ngày

            # Các điều kiện lọc kỹ thuật
            cond_ma = latest['close'] > latest['MA10']        # Giá nằm trên MA10
            cond_vol = actual_vol >= (VOL_FACTOR * latest['Vol_MA20'])  # Khối lượng vượt mức tiêu chuẩn
            cond_rsi = RSI_MIN <= latest['RSI'] <= RSI_MAX    # RSI hợp lý

            if cond_ma and cond_vol and cond_rsi:
                count_matches += 1
                price_vnd = latest['close'] * 1000
                vol_ratio = round(actual_vol / latest['Vol_MA20'], 1)
                
                msg = (
                    f"🚀 *[TÍN HIỆU TĂNG TRƯỞNG VN100]* 🚀\n\n"
                    f"📌 *Mã cổ phiếu:* `{ticker}`\n"
                    f"📈 *Giá đóng cửa:* {price_vnd:,.0f} VNĐ\n"
                    f"📊 *Vol thực tế:* {int(actual_vol):,} CP (Gấp *{vol_ratio}x* MA20)\n"
                    f"🎯 *Chỉ số RSI:* {latest['RSI']:.1f}\n"
                    f"⚡ *Đánh giá:* Vượt MA10 + Đột biến Vol + RSI trong vùng mua đẹp!"
                )
                
                print(f"✅ [{i+1}/{len(tickers)}] TÌM THẤY MÃ: {ticker} | Giá: {price_vnd:,.0f} | RSI: {latest['RSI']:.1f}")
                send_telegram(msg)

            time.sleep(1.0)  # Giảm sleep time xuống chút vì chạy cuối ngày không cần duy trì liên tục quá lâu

        except Exception as e:
            continue

    print(f"\n🎉 Quét xong! Tìm thấy tổng cộng {count_matches} mã thỏa mãn bộ lọc.")

if __name__ == "__main__":
    scan_quant_signals()
