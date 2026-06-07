r"""
parquet_to_excel.py

Parquet 파일을 Excel(.xlsx)로 변환합니다.

실행 예:
  python parquet_to_excel.py
  python parquet_to_excel.py --input naver_market_report_2026_06.parquet
  python parquet_to_excel.py --input report.parquet --output report.xlsx
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

EXCEL_MAX_ROWS = 1_048_576
DEFAULT_PARQUET = "naver_market_report_2026_06.parquet"

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def parquet_to_excel(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str] | None = None,
) -> str:
    input_path = os.path.abspath(input_path)
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Parquet 파일을 찾을 수 없습니다: {input_path}")

    if output_path is None:
        base, _ = os.path.splitext(input_path)
        output_path = base + ".xlsx"
    else:
        output_path = os.path.abspath(output_path)

    print(f"읽는 중: {input_path}")
    df = pd.read_parquet(input_path)
    row_count, col_count = len(df), len(df.columns)

    if row_count > EXCEL_MAX_ROWS:
        raise ValueError(
            f"행 수({row_count:,})가 Excel 최대 행 수({EXCEL_MAX_ROWS:,})를 초과합니다."
        )

    print(f"변환 중: {row_count:,}행 × {col_count}열 → {output_path}")
    df.to_excel(output_path, index=False, engine="openpyxl")

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"완료: {output_path} ({size_mb:.1f} MB)")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Parquet 파일을 Excel로 변환")
    parser.add_argument(
        "--input",
        "-i",
        default=os.path.join(_BASE_DIR, DEFAULT_PARQUET),
        help=f"입력 Parquet 경로 (기본: {DEFAULT_PARQUET})",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="출력 Excel 경로 (기본: 입력 파일명과 동일한 .xlsx)",
    )
    args = parser.parse_args()

    input_path = args.input
    if not os.path.isabs(input_path):
        input_path = os.path.join(_BASE_DIR, input_path)

    parquet_to_excel(input_path, args.output)


if __name__ == "__main__":
    main()
