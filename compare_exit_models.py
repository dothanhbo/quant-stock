from backtesting.engine import backtest_symbol, BacktestConfig
from backtesting.exit_models import FixedExitModel, ATRExitModel

config = BacktestConfig()

fixed_trades = backtest_symbol(
    "HPG",
    config,
    exit_model=FixedExitModel(),
    verbose=False,
)

atr_trades = backtest_symbol(
    "HPG",
    config,
    exit_model=ATRExitModel(),
    verbose=False,
)

print("=" * 50)
print("Fixed Exit")
print("=" * 50)
print(f"Trades: {len(fixed_trades)}")
for trade in fixed_trades:
    print(trade)

print()

print("=" * 50)
print("ATR Exit")
print("=" * 50)
print(f"Trades: {len(atr_trades)}")
for trade in atr_trades:
    print(trade)