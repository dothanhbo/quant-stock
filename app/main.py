from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
PYTHON_EXECUTABLE = sys.executable

STEPS = [
    ("Cập nhật dữ liệu VN100", "scripts.update_data", True),
    ("Cập nhật VNINDEX", "scripts.update_index", True),
    ("Cập nhật kết quả tín hiệu cũ", "scripts.update_signal_results", False),
    ("Quét tín hiệu và gửi Telegram", "strategy.scanner", True),
    ("Tạo báo cáo hiệu suất", "analysis.performance_report", False),
]

def run_module(name: str, module: str) -> bool:
    print("\n" + "=" * 70)
    print(f"🚀 {name}")
    print(f"Module: {module}")
    print("=" * 70)
    process = subprocess.run(
        [PYTHON_EXECUTABLE, "-m", module],
        cwd=PROJECT_DIR,
        check=False,
    )
    if process.returncode == 0:
        print(f"✅ Hoàn thành: {name}")
        return True
    print(f"❌ {name} kết thúc với mã lỗi {process.returncode}")
    return False

def main() -> int:
    started_at = datetime.now()
    print("\n" + "#" * 70)
    print("🤖 QUANT BOT BẮT ĐẦU")
    print(f"Thời gian: {started_at:%Y-%m-%d %H:%M:%S}")
    print("#" * 70)
    optional_failures: list[str] = []
    for name, module, required in STEPS:
        if run_module(name, module):
            continue
        if required:
            print("\n⛔ Bước bắt buộc thất bại. Dừng bot để tránh dùng dữ liệu lỗi.")
            return 1
        optional_failures.append(name)
    duration = datetime.now() - started_at
    print("\n" + "#" * 70)
    print("📋 TỔNG KẾT")
    print("#" * 70)
    print(f"Thời gian chạy: {duration.total_seconds():.1f} giây")
    if optional_failures:
        print("⚠️ Các bước phụ bị lỗi: " + ", ".join(optional_failures))
    print("✅ Quant Bot đã chạy hoàn tất.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())