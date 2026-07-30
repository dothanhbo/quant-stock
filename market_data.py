from vnstock.api.quote import Quote
import pandas as pd

from database import save_price_data


def get_history(symbol):

    try:

        quote = Quote(
            symbol=symbol,
            source="VCI"
        )


        df = quote.history(
            start="2025-01-01",
            end="2026-07-28",
            interval="1D"
        )


        if df is None or df.empty:
            print(symbol, "No data")
            return None


        df = df.rename(
            columns={
                "time":"time",
                "open":"open",
                "high":"high",
                "low":"low",
                "close":"close",
                "volume":"volume"
            }
        )


        return df



    except Exception as e:

        print(
            symbol,
            "ERROR:",
            e
        )

        return None



def update_symbol(symbol):

    df = get_history(symbol)


    if df is None:
        return False


    save_price_data(
        symbol,
        df
    )


    return True