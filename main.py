from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
PYTHON_EXECUTABLE = sys.executable


STEPS = [
    {
        "name": "Cập nhật dữ liệu VN100",
        "file": "update_data.py",
        "required": True,
    },
    {
        "name": "Cập nhật VNINDEX",
        "file": "update_index.py",
        "required": True,
    },
    {
        "name": "Cập nhật kết quả tín hiệu cũ",
        "file": "update_signal_results.py",
        "required": False,
    },
    {
        "name": "Quét tín hiệu và gửi Telegram",
        "file": "scanner.py",
        "required": True,
    },
    {
        "name": "Tạo báo cáo hiệu suất",
        "file": "performance_report.py",
        "required": False,
    },
]


def run_script(name: str, filename: str) -> bool:
    script_path = PROJECT_DIR / filename

    print("\n" + "=" * 70)
    print(f"🚀 {name}")
    print(f"File: {script_path}")
    print("=" * 70)

    if not script_path.exists():
        print(f"❌ Không tìm thấy file: {filename}")
        return False

    process = subprocess.run(
        [PYTHON_EXECUTABLE, str(script_path)],
        cwd=PROJECT_DIR,
        check=False,
    )

    if process.returncode == 0:
        print(f"✅ Hoàn thành: {name}")
        return True

    print(
        f"❌ {name} kết thúc với mã lỗi "
        f"{process.returncode}"
    )
    return False


def main() -> int:
    started_at = datetime.now()

    print("\n" + "#" * 70)
    print("🤖 QUANT BOT BẮT ĐẦU")
    print(f"Thời gian: {started_at:%Y-%m-%d %H:%M:%S}")
    print("#" * 70)

    optional_failures = []

    for step in STEPS:
        success = run_script(
            name=step["name"],
            filename=step["file"],
        )

        if success:
            continue

        if step["required"]:
            print(
                "\n⛔ Bước bắt buộc thất bại. "
                "Dừng bot để tránh dùng dữ liệu lỗi."
            )
            return 1

        optional_failures.append(step["name"])

    duration = datetime.now() - started_at

    print("\n" + "#" * 70)
    print("📋 TỔNG KẾT")
    print("#" * 70)
    print(f"Thời gian chạy: {duration.total_seconds():.1f} giây")

    if optional_failures:
        print(
            "⚠️ Các bước phụ bị lỗi: "
            + ", ".join(optional_failures)
        )

    print("✅ Quant Bot đã chạy hoàn tất.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
