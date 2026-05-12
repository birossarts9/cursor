from __future__ import annotations

from pathlib import Path
import re

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
FILE_PATTERNS = ("naver_market_report_*.parquet", "naver_market_report_*.xlsx")


def clean_realtor_name(name: object) -> str:
    pattern = r"공인중개사사무소|공인중개사|중개사무소|부동산|중개사|공인|중개|사무소"
    cleaned = re.sub(pattern, "", str(name))
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned if cleaned else str(name)


def find_latest_report_file() -> Path:
    candidates: list[Path] = []
    for base in (DATA_DIR, ROOT_DIR):
        if not base.exists():
            continue
        for pattern in FILE_PATTERNS:
            candidates.extend(base.glob(pattern))
    if not candidates:
        raise FileNotFoundError("리포트 파일을 찾지 못했습니다.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_report(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_excel(path)


def run_collision_analysis(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = ["부동산명", "단지명", "동/호수", "층/타입", "거래방식", "고유번호"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"필수 컬럼 누락: {missing}")

    work = df.copy()
    if "수집일시" in work.columns:
        work["수집일시"] = pd.to_datetime(work["수집일시"], errors="coerce")
    if "확인일자" in work.columns:
        work["확인일자"] = work["확인일자"].astype(str).str.strip()
        work.loc[work["확인일자"].isin(["", "nan", "NaT", "None"]), "확인일자"] = pd.NA

    work["정제부동산명"] = work["부동산명"].apply(clean_realtor_name)
    work["물리적스펙키"] = (
        work["단지명"].fillna("").astype(str).str.strip()
        + " | "
        + work["동/호수"].fillna("").astype(str).str.strip()
        + " | "
        + work["층/타입"].fillna("").astype(str).str.strip()
        + " | "
        + work["거래방식"].fillna("").astype(str).str.strip()
    )
    work["고유번호"] = work["고유번호"].fillna("").astype(str).str.strip()
    work = work[work["고유번호"] != ""].copy()

    # 조건 1) 동일 수집일시 동시노출 내 충돌
    by_collect = work.dropna(subset=["수집일시"]).copy()
    g1 = (
        by_collect.groupby(["수집일시", "정제부동산명", "물리적스펙키"], dropna=False)
        .agg(
            고유번호수=("고유번호", "nunique"),
            행수=("고유번호", "size"),
            고유번호목록=("고유번호", lambda s: " | ".join(sorted(set(s)))),
        )
        .reset_index()
    )
    c1 = g1[g1["고유번호수"] >= 2].sort_values(["고유번호수", "행수"], ascending=[False, False])

    # 조건 2) 수집일시가 비어 있거나 확인 보조 비교가 필요할 때, 동일 확인일자 내 충돌
    by_confirm = work.dropna(subset=["확인일자"]).copy()
    g2 = (
        by_confirm.groupby(["확인일자", "정제부동산명", "물리적스펙키"], dropna=False)
        .agg(
            고유번호수=("고유번호", "nunique"),
            행수=("고유번호", "size"),
            고유번호목록=("고유번호", lambda s: " | ".join(sorted(set(s)))),
        )
        .reset_index()
    )
    c2 = g2[g2["고유번호수"] >= 2].sort_values(["고유번호수", "행수"], ascending=[False, False])

    return c1, c2


def main() -> None:
    path = find_latest_report_file()
    df = load_report(path)
    print(f"[로드] {path.name} / rows={len(df):,}")

    c_collect, c_confirm = run_collision_analysis(df)
    work = df.copy()
    work["정제부동산명"] = work["부동산명"].apply(clean_realtor_name)
    work["물리적스펙키"] = (
        work["단지명"].fillna("").astype(str).str.strip()
        + " | "
        + work["동/호수"].fillna("").astype(str).str.strip()
        + " | "
        + work["층/타입"].fillna("").astype(str).str.strip()
        + " | "
        + work["거래방식"].fillna("").astype(str).str.strip()
    )
    if "수집일시" in work.columns:
        work["수집일시"] = pd.to_datetime(work["수집일시"], errors="coerce")
    if "확인일자" in work.columns:
        work["확인일자"] = work["확인일자"].astype(str).str.strip()
        work.loc[work["확인일자"].isin(["", "nan", "NaT", "None"]), "확인일자"] = pd.NA

    print("\n[동일 수집일시 기준 충돌]")
    print(f"- 충돌 그룹 수: {len(c_collect):,}")
    if not c_collect.empty:
        print("- 상위 10개:")
        print(c_collect.head(10).to_string(index=False))

    print("\n[동일 확인일자 기준 충돌]")
    print(f"- 충돌 그룹 수: {len(c_confirm):,}")
    if not c_confirm.empty:
        print("- 상위 10개:")
        print(c_confirm.head(10).to_string(index=False))

    # 눈으로 확인할 수 있도록 충돌 원본행만 추출해 엑셀 저장
    out_dir = ROOT_DIR / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not c_collect.empty:
        key_collect = c_collect[["수집일시", "정제부동산명", "물리적스펙키"]].drop_duplicates()
        rows_collect = work.merge(
            key_collect,
            on=["수집일시", "정제부동산명", "물리적스펙키"],
            how="inner",
        ).sort_values(["수집일시", "정제부동산명", "물리적스펙키", "고유번호"])
        collect_path = out_dir / "collision_rows_same_collect_time.xlsx"
        rows_collect.to_excel(collect_path, index=False)
        print(f"\n[파일 저장] {collect_path}")
        print(f"- 저장 행 수(수집일시 기준 충돌 원본): {len(rows_collect):,}")

    if not c_confirm.empty:
        key_confirm = c_confirm[["확인일자", "정제부동산명", "물리적스펙키"]].drop_duplicates()
        rows_confirm = work.merge(
            key_confirm,
            on=["확인일자", "정제부동산명", "물리적스펙키"],
            how="inner",
        ).sort_values(["확인일자", "정제부동산명", "물리적스펙키", "고유번호"])
        confirm_path = out_dir / "collision_rows_same_confirm_date.xlsx"
        rows_confirm.to_excel(confirm_path, index=False)
        print(f"[파일 저장] {confirm_path}")
        print(f"- 저장 행 수(확인일자 기준 충돌 원본): {len(rows_confirm):,}")


if __name__ == "__main__":
    main()
