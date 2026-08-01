from vnstock import Vnstock

stock_api = Vnstock()


def get_vn100_symbols():
    try:
        result = (
            stock_api
            .stock(
                symbol="ACB",
                source="VCI"
            )
            .listing
            .symbols_by_group("VN100")
        )

        if hasattr(result, "columns"):

            if "ticker" in result.columns:
                return result["ticker"].tolist()

            if "symbol" in result.columns:
                return result["symbol"].tolist()

        if hasattr(result, "tolist"):
            return result.tolist()

        return list(result)

    except Exception as e:
        print(
            "❌ Không lấy được VN100:",
            e
        )

        return []