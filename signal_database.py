from sqlalchemy import text

from database import engine


def save_signal(signal):
    """
    Lưu một tín hiệu vào bảng signals.

    Return:
        True: đã thêm tín hiệu mới.
        False: tín hiệu đã tồn tại.
    """

    payload = {
        "signal_date": signal["date"],
        "symbol": signal["symbol"],
        "score": signal.get("score"),
        "entry": signal.get("entry"),
        "stop_loss": signal.get("stop_loss"),
        "take_profit": signal.get("take_profit"),
        "rsi": signal.get("rsi"),
        "adx": signal.get("adx"),
        "volume_ratio": signal.get(
            "volume_ratio"
        ),
        "relative_strength": signal.get(
            "relative_strength_20d"
        )
    }

    check_query = text("""
        SELECT id
        FROM signals
        WHERE signal_date = :signal_date
          AND symbol = :symbol
        LIMIT 1
    """)

    insert_query = text("""
        INSERT INTO signals (
            signal_date,
            symbol,
            score,
            entry,
            stop_loss,
            take_profit,
            rsi,
            adx,
            volume_ratio,
            relative_strength,
            status
        )
        VALUES (
            :signal_date,
            :symbol,
            :score,
            :entry,
            :stop_loss,
            :take_profit,
            :rsi,
            :adx,
            :volume_ratio,
            :relative_strength,
            'OPEN'
        )
    """)

    with engine.begin() as connection:
        existing_id = connection.execute(
            check_query,
            {
                "signal_date": payload[
                    "signal_date"
                ],
                "symbol": payload["symbol"]
            }
        ).scalar()

        if existing_id is not None:
            return False

        connection.execute(
            insert_query,
            payload
        )

    return True