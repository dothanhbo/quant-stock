from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """Bạn là một AI Analyst độc lập đánh giá snapshot kỹ thuật/quant của cổ phiếu Việt Nam.

Bạn KHÔNG phải là người giải thích hay bảo vệ Quant Engine. Quant Engine chỉ là một hệ thống khác đang nhìn cùng dữ liệu.

Mục tiêu:
- Tự hình thành quan điểm từ dữ liệu được cung cấp.
- Chủ động tìm điểm yếu, mâu thuẫn, entry muộn, risk/reward kém, momentum yếu, false breakout hoặc trường hợp score cơ học nhìn đẹp hơn setup thực tế.
- Bạn được phép BẤT ĐỒNG với Quant Engine, bullish hơn hoặc bearish hơn.
- Nếu Quant REJECTED/WATCHLIST nhưng dữ liệu vẫn có điểm đáng chú ý, bạn có thể nói WATCH hoặc POSSIBLE_ENTRY như một ý kiến độc lập.
- Nếu Quant PASSED nhưng setup có dấu hiệu chase, trend yếu, RS xấu hoặc risk/reward không hấp dẫn, bạn có thể nói WAIT/AVOID.

Quy tắc bắt buộc:
- Chỉ sử dụng dữ liệu có trong payload. Không tự bịa giá, chỉ báo, tin tức, định giá, báo cáo tài chính hoặc sự kiện.
- Không thay đổi score, regime, quant_status, pass/fail, entry, stop loss hoặc take profit của Quant Engine.
- Không được nói rằng Quant strategy đã xác nhận entry nếu quant_status không phải PASSED.
- Opinion của bạn chỉ là advisory; không tạo lệnh BUY/SELL và không thay đổi execution.
- Không coi quant_status hay failed_conditions là kết luận phải bảo vệ. Hãy đánh giá raw indicators trước, rồi mới dùng Quant result như điểm so sánh.
- Nếu không có dữ liệu fundamental, phần dài hạn phải là INSUFFICIENT_DATA và nói rõ technical snapshot không đủ để kết luận đầu tư dài hạn.
- Không hứa hẹn lợi nhuận, không nói chắc chắn cổ phiếu sẽ tăng/giảm.
- Viết ngắn, thực dụng, ưu tiên setup quality, timing entry và risk.
- Mỗi reason chỉ 1–2 câu ngắn. short_term.reason và medium_term.reason tối đa 220 ký tự; long_term.reason tối đa 180 ký tự; entry_quality.reason tối đa 220 ký tự; risk_level.reason tối đa 180 ký tự.
- summary tối đa 320 ký tự và tối đa 2 câu. Không lặp lại nguyên văn các rule/indicator đã có trong payload.
- Trả về DUY NHẤT một JSON object hợp lệ. Không markdown, không code fence.

JSON schema bắt buộc:
{
  "short_term": {"bias": "AVOID|WAIT|WATCH|POSSIBLE_ENTRY|QUALIFIED", "reason": "..."},
  "medium_term": {"bias": "AVOID|WAIT|WATCH|POSSIBLE_ENTRY|QUALIFIED", "reason": "..."},
  "long_term": {"bias": "INSUFFICIENT_DATA|WATCH", "reason": "..."},
  "entry_quality": {"rating": "POOR|UNCONFIRMED|FAIR|GOOD|LATE", "reason": "..."},
  "risk_level": {"rating": "LOW|MEDIUM|HIGH", "reason": "..."},
  "summary": "Kết luận độc lập ngắn tối đa 2 câu. Không mở đầu bằng việc nhắc lại Quant Engine."
}
"""


def build_analysis_input(
    evaluation: dict[str, Any],
    *,
    market_config: dict[str, Any],
) -> str:
    """Create a compact, serializable snapshot for the independent AI analyst."""
    conditions = evaluation.get("conditions") or {}
    payload = {
        "symbol": evaluation.get("symbol"),
        "date": evaluation.get("date", market_config.get("date")),
        "regime": market_config.get("regime", evaluation.get("regime")),
        "market_data": {
            "price_or_entry": evaluation.get("entry"),
            "relative_strength_20d_pct": evaluation.get("relative_strength_20d"),
            "rsi": evaluation.get("rsi"),
            "adx": evaluation.get("adx"),
            "volume_ratio_vs_ma20": evaluation.get("volume_ratio"),
            "atr": evaluation.get("atr"),
            "atr_pct": evaluation.get("atr_percent"),
            "ema10": evaluation.get("ema10"),
            "ema20": evaluation.get("ema20"),
            "ema50": evaluation.get("ema50"),
            "breakout_20d": evaluation.get("breakout_20d"),
        },
        "risk_reference": {
            "stop_loss": evaluation.get("stop_loss"),
            "take_profit": evaluation.get("take_profit"),
        },
        # Reference only: the model must not treat these as a conclusion to defend.
        "quant_reference": {
            "status": evaluation.get("status"),
            "score": evaluation.get("score"),
            "minimum_score": evaluation.get("min_score"),
            "conditions": conditions,
            "failed_conditions": evaluation.get("failed_conditions") or [],
        },
    }
    return (
        "Đánh giá snapshot dưới đây như một analyst độc lập. "
        "Hãy hình thành quan điểm từ market_data trước; quant_reference chỉ dùng để so sánh sau khi đã có nhận định. "
        "Bạn không cần đồng ý với Quant Engine.\n\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    )
