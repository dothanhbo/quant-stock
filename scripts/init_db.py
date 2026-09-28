from __future__ import annotations

from core.database import initialize_market_database


def main() -> None:
    """Initialize the canonically resolved market database explicitly."""
    initialize_market_database()
    print("Database created!")


if __name__ == "__main__":
    main()
