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
# 2. THAM SỐ LỌC KỸ THUẬT & QUẢN TRỊ RỦI RO
# ==========================================
VOL_FACTOR = 1.2
RSI_MIN = 45
RSI_MAX = 70
RR_RATIO = 2.0  # Tỷ lệ Reward/Risk chuẩn = 2 (Lời gấp đôi Lỗ)

# ==========================================
# 3. CÁC HÀM BỔ TRỢ
# ==========================================
def get_vn100_symbols():
    """Lấy danh sách mã VN100 chuẩn từ class Vnstock"""
    try:
        stock = Vnstock()
        df_symbols = stock.stock(symbol='ACB', source='VCI').listing.symbols_by_group('VN100')
        if isinstance(df_symbols, pd.DataFrame):
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
# 4. YÊU CẦU 1: KIỂM TRA BỘ LỌC VN-INDEX
# ==========================================
def check_vnindex_health():
    """Lọc thị trường chung: Trả về True nếu VN-Index nằm trên MA20"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    start_str = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    
    try:
        # Lấy dữ liệu VN-Index
        q = Quote(symbol='VNINDEX', source='VCI')
        df = q.history(start=start_str, end=today_str, interval='1D')
        
        if df is None or df.empty or len(df) < 20:
            print("⚠️ Không lấy được dữ liệu VN-Index, bỏ qua bước lọc thị trường chung.")
            return True
            
        df['MA20'] = df['close'].rolling(window=20).mean()
        latest = df.iloc[-1]
        
        vni_close = latest['close']
        vni_ma20 = latest['MA20']
        
        print(f"📊 VN-Index Hiện tại: {vni_close:.2f} | MA20: {vni_ma20:.2f}")
        
        if vni_close < vni_ma20:
            msg_alert = (
                "🚨 *[CẢNH BÁO THỊ TRƯỜNG CHUNG]* 🚨\n\n"
                f"📌 *VN-Index:* `{vni_close:.2f}` (Dưới MA20 `{vni_ma20:.2f}`)\n"
                "⚠️ *Đánh giá:* Xu hướng thị trường chung đang đi vào vùng rủi ro/Downtrend ngắn hạn.\n"
                "⛔ *Hành động:* Bot dừng quét để bảo vệ vốn. **Không khuyến nghị mở vị thế mua mới!**"
            )
            print("❌ VN-Index nằm dưới MA20. Dừng hệ thống quét!")
            send_telegram(msg_alert)
            return False
            
        print("✅ Thị trường chung VN-Index an toàn (Đang nằm trên MA20). Tiến hành quét mã cổ phiếu...\n")
        return True
        
    except Exception as e:
        print(f"⚠️ Lỗi khi kiểm tra VN-Index: {e}")
        return True # Giữ an toàn vẫn cho quét nếu API VNINDEX bị nghẽn

# ==========================================
# 5. HÀM SCAN CHÍNH
# ==========================================
def scan_quant_signals():
    # Bước 1: Kiểm tra VN-Index trước
    if not check_vnindex_health():
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    start_str = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    tickers = get_vn100_symbols()
    print(f"🔍 Bắt đầu quét {len(tickers)} cổ phiếu VN100...")
    
    count_matches = 0

    for i, ticker in enumerate(tickers):
        try:
            q = Quote(symbol=ticker, source='VCI')
            df = q.history(start=start_str, end=today_str, interval='1D')
            
            if df is None or df.empty or len(df) < 25:
                continue

            # Tính toán chỉ báo Kỹ thuật
            df['MA10'] = df['close'].rolling(window=10).mean()
            df['MA20'] = df['close'].rolling(window=20).mean()
            df['Vol_MA20'] = df['volume'].rolling(window=20).mean()
            df['RSI'] = calculate_rsi(df['close'], period=14)

            latest = df.iloc[-1]
            actual_vol = latest['volume']

            # Điều kiện kỹ thuật cơ bản
            cond_ma = latest['close'] > latest['MA10']
            cond_vol = actual_vol >= (VOL_FACTOR * latest['Vol_MA20'])
            cond_rsi = RSI_MIN <= latest['RSI'] <= RSI_MAX

            # ==========================================
            # YÊU CẦU 2: ĐIỀU KIỆN NỀN GIÁ (PRICE ACTION)
            # ==========================================
            open_p = latest['open']
            close_p = latest['close']
            high_p = latest['high']
            low_p = latest['low']
            
            is_green_candle = close_p > open_p  # Thân nến xanh
            
            # Kiểm tra giá nằm ở nửa trên thanh nến (Tránh nến bị xả râu trên)
            candle_range = high_p - low_p
            upper_half = low_p + (candle_range * 0.5)
            is_upper_half = close_p >= upper_half if candle_range > 0 else True
            
            cond_price_action = is_green_candle and is_upper_half

            # LỌC TẤT CẢ ĐIỀU KIỆN
            if cond_ma and cond_vol and cond_rsi and cond_price_action:
                count_matches += 1
                price_vnd = close_p * 1000
                vol_ratio = round(actual_vol / latest['Vol_MA20'], 1)
                
                # ==========================================
                # YÊU CẦU 3: LẬP PLAN QUẢN TRỊ RỦI RO (SL / TP)
                # ==========================================
                ma20_vnd = latest['MA20'] * 1000
                stop_loss = ma20_vnd * 0.99  # Cắt lỗ khi thủng dưới MA20 khoảng 1%
                risk_per_share = price_vnd - stop_loss
                
                # Bắt đệm an toàn nếu giá sát MA20 quá (tránh Risk <= 0)
                if risk_per_share <= 0:
                    stop_loss = price_vnd * 0.95 # Cắt lỗ mặc định 5%
                    risk_per_share = price_vnd - stop_loss
                
                # Mức chốt lời mục tiêu theo Tỷ lệ R:R = 2.0
                take_profit = price_vnd + (risk_per_share * RR_RATIO)
                
                pct_sl = round(((stop_loss - price_vnd) / price_vnd) * 100, 1)
                pct_tp = round(((take_profit - price_vnd) / price_vnd) * 100, 1)

                msg = (
                    f"🚀 *[TÍN HIỆU QUANT TĂNG TRƯỞNG VN100]* 🚀\n\n"
                    f"📌 *Mã cổ phiếu:* `{ticker}`\n"
                    f"📈 *Giá Mua (Entry):* {price_vnd:,.0f} VNĐ\n"
                    f"📊 *Vol thực tế:* {int(actual_vol):,} CP (Gấp *{vol_ratio}x* MA20)\n"
                    f"🎯 *Chỉ số RSI:* {latest['RSI']:.1f}\n"
                    f"🕯️ *Nền giá:* Nến xanh thân khỏe, không dính râu xả!\n\n"
                    f"🛡️ *KE HOẠCH QUẢN TRỊ RỦI RO (R:R = 1:{RR_RATIO}):*\n"
                    f"🛑 *Cắt lỗ (Stoploss):* {stop_loss:,.0f} VNĐ ({pct_sl}% - Gãy MA20)\n"
                    f"🎯 *Chốt lời (Take Profit):* {take_profit:,.0f} VNĐ (+{pct_tp}%)\n"
                )
                
                print(f"✅ [{i+1}/{len(tickers)}] TÌM THẤY MÃ: {ticker} | Giá: {price_vnd:,.0f} | RSI: {latest['RSI']:.1f}")
                send_telegram(msg)

            time.sleep(3)

        except Exception as e:
            continue

    print(f"\n🎉 Quét xong! Tìm thấy tổng cộng {count_matches} mã thỏa mãn bộ lọc.")

if __name__ == "__main__":
    scan_quant_signals()
