from scanner import check_signal


def run_test(symbol, end_date):
    print("\n" + "=" * 60)
    print(f"Symbol: {symbol}")
    print(f"End date: {end_date}")

    signal = check_signal(
        symbol=symbol,
        end_date=end_date
    )

    if signal is None:
        print("Kết quả: Không có tín hiệu")
        return

    print("Kết quả: Có tín hiệu")
    print(signal)


if __name__ == "__main__":
    run_test("HPG", "2025-10-01")
    run_test("HPG", "2026-01-15")
    run_test("HPG", "2026-04-15")
    run_test("HPG", "2026-07-28")