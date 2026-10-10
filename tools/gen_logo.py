#!/usr/bin/env python3
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from beats_logo import write_app_icon_svg  # noqa: E402

OUT = ROOT / "packaging/usr/share/icons/hicolor/scalable/apps/beats.svg"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    write_app_icon_svg(OUT)
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
