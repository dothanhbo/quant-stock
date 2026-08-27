from services.telegram_bot.bot import parse_symbol_request


def test_parse_plain_symbol() -> None:
    assert parse_symbol_request("fpt") == "FPT"


def test_parse_check_command() -> None:
    assert parse_symbol_request("/check hpg") == "HPG"


def test_parse_invalid_request() -> None:
    assert parse_symbol_request("/check") is None
    assert parse_symbol_request("FPT HPG") is None
    assert parse_symbol_request("/help") is None
