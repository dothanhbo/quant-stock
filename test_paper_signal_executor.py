from dotenv import load_dotenv

from execution.signal_executor import (
    PaperSignalExecutor,
)


def main() -> None:
    load_dotenv()

    executor = PaperSignalExecutor.from_env()

    signals = [
        {
            "symbol": "HPG",
            "entry": 25.0,
            "score": 80,
        },
        {
            "symbol": "FPT",
            "entry": 120.0,
            "score": 78,
        },
    ]

    result = executor.execute_signals(
        signals
    )

    print(result)

    for execution in result.executions:
        print(execution)


if __name__ == "__main__":
    main()
