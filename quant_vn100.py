import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from vnstock import Vnstock

# ==========================================
# 1. CẤU HÌNH BOT TELEGRAM
# ==========================================
TELEGRAM_TOKEN = "8860199022:AAHNtR2Xd5eekkzRvG_ILrslvrc4pKNwd2I"
CHAT_ID = "5137019839"

# ==========================================
# 2. THAM SỐ LỌC KỸ THUẬT & QUẢN TRỊ RỦI RO
# ==========================================
VOL_FACTOR_DEFAULT = 1.2  # Hệ số Vol chuẩn khi VNI đẹp
RSI_MIN = 45
RSI_MAX = 70
RR_RATIO = 2.0  # Tỷ lệ Reward/Risk chuẩn = 2.0

stock_api = Vnstock()

# ==========================================
# 3. CÁC HÀM BỔ TRỢ
# ==========================================
def get_vn100_symbols():
    """Lấy danh sách mã VN100 chuẩn từ class Vnstock"""
    try:
        df_symbols = stock_api.stock(symbol='ACB', source='VCI').listing.symbols_by_group('VN100')
        if isinstance(df_symbols, pd.DataFrame) and 'ticker' in df_symbols.columns:
            return df_symbols['ticker'].tolist()
        return list(df_symbols)
    except Exception as e:
        print(f"⚠️ Lỗi lấy danh sách VN100 tự động, dùng danh sách dự phòng: {e}")
        return [
            "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
            "MBB", "MSN", "MWG", "PLX", "POW", "SAB", "SSB", "SSI", "STB", "TCB",
            "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VRE", "AAA"
        ]

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
# 4. YÊU CẦU 1: KIỂM TRA BỘ LỌC VN-INDEX (EMA20)
# ==========================================
def check_vnindex_health():
    """Lọc thị trường chung theo EMA20 nhạy bén hơn"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    start_str = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    
    try:
        df = stock_api.stock(symbol='VNINDEX', source='VCI').quote.history(start=start_str, end=today_str, interval='1D')
        
        if df is None or df.empty or len(df) < 20:
            print("⚠️ Không lấy được dữ liệu VN-Index, mặc định chế độ an toàn.")
            return True, VOL_FACTOR_DEFAULT, "100% NAV"
            
        # Đổi sang EMA20 để nhạy hơn với biến động ngắn hạn
        df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
        latest = df.iloc[-1]
        
        vni_close = latest['close']
        vni_ema20 = latest['EMA20']
        
        print(f"📊 VN-Index Hiện tại: {vni_close:.2f} | EMA20: {vni_ema20:.2f}")
        
        if vni_close < vni_ema20:
            print("⚠️ VN-Index dưới EMA20: Chuyển sang chế độ SIẾT LỌC (Thăm dò tỷ trọng nhỏ).")
            # Trả về: (is_safe, vol_factor_khắt_khe_hơn, tỷ_trọng_khuyên_dùng)
            return True, 1.5, "30% - 50% NAV (Đánh thăm dò/Lướt nhanh)"
            
        print("✅ VN-Index an toàn (Trên EMA20). Tiến hành quét mã cổ phiếu...\n")
        return True, VOL_FACTOR_DEFAULT, "100% NAV (Thị trường thuận lợi)"
        
    except Exception as e:
        print(f"⚠️ Lỗi khi kiểm tra VN-Index: {e}")
        return True, VOL_FACTOR_DEFAULT, "100% NAV"

# ==========================================
# 5. HÀM SCAN CHÍNH
# ==========================================
def scan_quant_signals():
    # Kiểm tra VN-Index và nhận tham số điều chỉnh động
    is_active, current_vol_factor, nav_recommendation = check_vnindex_health()
    if not is_active:
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    start_str = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    tickers = get_vn100_symbols()
    print(f"🔍 Bắt đầu quét {len(tickers)} cổ phiếu VN100 (Vol Factor = {current_vol_factor}x)...")
    
    count_matches = 0

    for i, ticker in enumerate(tickers):
        try:
            df = stock_api.stock(symbol=ticker, source='VCI').quote.history(start=start_str, end=today_str, interval='1D')
            
            if df is None or df.empty or len(df) < 25:
                continue

            # Chuyển sang tính chỉ báo EMA để tăng độ nhạy
            df['EMA10'] = df['close'].ewm(span=10, adjust=False).mean()
            df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
            df['Vol_MA20'] = df['volume'].rolling(window=20).mean()
            df['RSI'] = calculate_rsi(df['close'], period=14)

            latest = df.iloc[-1]
            actual_vol = latest['volume']

            # Điều kiện kỹ thuật ngắn hạn (EMA10 > EMA20 & Giá > EMA10)
            cond_ma = (latest['close'] > latest['EMA10']) and (latest['EMA10'] > latest['EMA20'])
            cond_vol = actual_vol >= (current_vol_factor * latest['Vol_MA20'])
            cond_rsi = RSI_MIN <= latest['RSI'] <= RSI_MAX

            # Điều kiện Nền giá (Price Action)
            open_p, close_p = latest['open'], latest['close']
            high_p, low_p = latest['high'], latest['low']
            
            is_green_candle = close_p > open_p
            candle_range = high_p - low_p
            upper_half = low_p + (candle_range * 0.5)
            is_upper_half = close_p >= upper_half if candle_range > 0 else True
            
            cond_price_action = is_green_candle and is_upper_half

            # LỌC TẤT CẢ ĐIỀU KIỆN
            if cond_ma and cond_vol and cond_rsi and cond_price_action:
                count_matches += 1
                price_vnd = close_p * 1000 if close_p < 1000 else close_p
                vol_ratio = round(actual_vol / latest['Vol_MA20'], 1)
                
                # Quản trị rủi ro dựa trên đường EMA20
                ema20_vnd = latest['EMA20'] * 1000 if latest['EMA20'] < 1000 else latest['EMA20']
                stop_loss = ema20_vnd * 0.99  # Cắt lỗ khi thủng dưới EMA20 khoảng 1%
                risk_per_share = price_vnd - stop_loss
                
                if risk_per_share <= 0:
                    stop_loss = price_vnd * 0.95
                    risk_per_share = price_vnd - stop_loss
                
                take_profit = price_vnd + (risk_per_share * RR_RATIO)
                
                pct_sl = round(((stop_loss - price_vnd) / price_vnd) * 100, 1)
                pct_tp = round(((take_profit - price_vnd) / price_vnd) * 100, 1)

                msg = (
                    f"🚀 *[TÍN HIỆU QUANT NHẠY BÁN VN100]*\n\n"
                    f"📌 *Mã cổ phiếu:* `{ticker}`\n"
                    f"📈 *Giá Mua (Entry):* {price_vnd:,.0f} VNĐ\n"
                    f"📊 *Vol thực tế:* {int(actual_vol):,} CP (Gấp *{vol_ratio}x* Vol MA20)\n"
                    f"🎯 *RSI (14):* {latest['RSI']:.1f}\n"
                    f"⚡ *Xếp chồng Trend:* Giá > EMA10 > EMA20\n"
                    f"💡 *Tỷ trọng đề xuất:* `{nav_recommendation}`\n\n"
                    f"🛡️ *KE HOẠCH QUẢN TRỊ RỦI RO (R:R = 1:{RR_RATIO}):*\n"
                    f"🛑 *Cắt lỗ (Stoploss):* {stop_loss:,.0f} VNĐ ({pct_sl}% - Gãy EMA20)\n"
                    f"🎯 *Chốt lời (Take Profit):* {take_profit:,.0f} VNĐ (+{pct_tp}%)\n"
                )
                
                print(f"✅ [{i+1}/{len(tickers)}] TÌM THẤY MÃ: {ticker} | Giá: {price_vnd:,.0f} | RSI: {latest['RSI']:.1f}")
                send_telegram(msg)

            time.sleep(3)  # Giảm delay xuống 1 giây để quét nhanh hơn

        except Exception as e:
            continue

    print(f"\n🎉 Quét xong! Tìm thấy tổng cộng {count_matches} mã thỏa mãn bộ lọc.")

if __name__ == "__main__":
    scan_quant_signals()
