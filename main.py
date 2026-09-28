from scripts.run_daily import main
from quantctl.run_history import run_tracked_entrypoint


if __name__ == "__main__":
    raise SystemExit(run_tracked_entrypoint("daily", main))
