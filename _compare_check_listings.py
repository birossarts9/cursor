# -*- coding: utf-8 -*-
"""need to check.xlsx vs naver_market_report_2026_05.parquet — 마지막 수집일 대조."""
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def _parse_excel_label(label: str) -> tuple[str, str, str, str, str]:
    label = str(label).strip()
    dong_m = re.match(r"^([^(]+)", label)
    dong = dong_m.group(1).strip() if dong_m else ""
    inner_m = re.search(r"\(([^)]+)\)", label)
    mid = inner_m.group(1) if inner_m else ""
    parts = [p.strip() for p in mid.split("|")]
    spec = parts[0] if parts else ""
    price = parts[1] if len(parts) > 1 else ""
    direction = ""
    dir_m = re.search(r"(남동향|남서향|북동향|북서향|동향|서향|남향|북향)\s*$", label)
    if dir_m:
        direction = dir_m.group(1)
    area, floor = "", ""
    if "·" in spec:
        area, floor = [x.strip().replace("층", "") for x in spec.split("·", 1)]
    elif "/" in spec:
        area = spec.replace("층", "").strip()
    return dong, area, floor, price, direction


def _price_digits(price_txt: str) -> str:
    return re.sub(r"[^\d]", "", str(price_txt or ""))


def _match_rows(p_may: pd.DataFrame, danji: str, label: str) -> pd.DataFrame:
    dong, area, floor, price, direction = _parse_excel_label(label)
    sub = p_may[p_may["단지명"].astype(str).str.strip() == danji].copy()
    if sub.empty:
        return sub
    sub = sub[sub["동/호수"].astype(str).str.strip() == dong]
    if area:
        sub = sub[sub["층/타입"].astype(str).str.contains(re.escape(area), regex=True, na=False)]
    if floor:
        sub = sub[sub["층/타입"].astype(str).str.contains(re.escape(floor), regex=True, na=False)]
    if direction:
        sub = sub[sub["층/타입"].astype(str).str.contains(direction, regex=False, na=False)]
    if price:
        pdig = _price_digits(price)
        if pdig:
            sub = sub[
                sub["가격"].astype(str).map(_price_digits).eq(pdig)
                | sub["가격"].astype(str).str.replace(r"[^\d]", "", regex=True).eq(pdig)
            ]
    return sub


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    checklist = pd.read_excel(ROOT / "need to check.xlsx")
    checklist.columns = ["단지명", "매물명"]
    checklist["단지명"] = checklist["단지명"].astype(str).str.strip()
    checklist["매물명"] = checklist["매물명"].astype(str).str.strip()

    p = pd.read_parquet(ROOT / "naver_market_report_2026_05.parquet")
    p["수집일시"] = pd.to_datetime(p["수집일시"], errors="coerce")
    p_may = p[
        p["수집일시"].notna()
        & (p["수집일시"].dt.year == 2026)
        & (p["수집일시"].dt.month == 5)
    ].copy()

    parquet_max = p_may["수집일시"].max()
    rows = []
    for i, row in checklist.iterrows():
        danji, label = row["단지명"], row["매물명"]
        hit = _match_rows(p_may, danji, label)
        if hit.empty:
            hit2 = _match_rows(p_may, danji, label)
            # 가격 조건 제외 재시도
            dong, area, floor, _, direction = _parse_excel_label(label)
            hit2 = p_may[p_may["단지명"] == danji]
            hit2 = hit2[hit2["동/호수"].astype(str).str.strip() == dong]
            if area:
                hit2 = hit2[hit2["층/타입"].astype(str).str.contains(re.escape(area), regex=True, na=False)]
            if floor:
                hit2 = hit2[hit2["층/타입"].astype(str).str.contains(re.escape(floor), regex=True, na=False)]
            if direction:
                hit2 = hit2[hit2["층/타입"].astype(str).str.contains(direction, regex=False, na=False)]
            note = "not_found" if hit2.empty else "no_price_match"
            hit = hit2
        else:
            note = "spec_match"

        if hit.empty:
            rows.append(
                {
                    "no": i + 1,
                    "단지명": danji,
                    "매물명": label,
                    "매칭": "not_found",
                    "마지막수집일": "",
                    "마지막수집시각": "",
                    "5월수집일수": 0,
                    "비고": "5월 파케이 미매칭",
                }
            )
            continue

        last_ts = hit["수집일시"].max()
        n_days = hit["수집일시"].dt.date.nunique()
        remark = note
        if last_ts.date() < parquet_max.date():
            remark += f"; 파케이 최신({parquet_max.date()})보다 이전 종료"
        rows.append(
            {
                "no": i + 1,
                "단지명": danji,
                "매물명": label,
                "매칭": note,
                "마지막수집일": last_ts.strftime("%Y-%m-%d"),
                "마지막수집시각": last_ts.strftime("%H:%M"),
                "5월수집일수": int(n_days),
                "비고": remark,
            }
        )

    result = pd.DataFrame(rows)
    out = ROOT / "need_to_check_last_collect.csv"
    result.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"파케이 5월 구간: {p_may['수집일시'].min()} ~ {p_may['수집일시'].max()}")
    print(f"결과 저장: {out}\n")
    print(result.to_string(index=False))
    print("\n--- 마지막수집일 분포 ---")
    print(result["마지막수집일"].value_counts().sort_index().to_string())
    stale = result[
        (result["마지막수집일"] != "")
        & (result["마지막수집일"] < parquet_max.strftime("%Y-%m-%d"))
    ]
    if not stale.empty:
        print(f"\n--- 파케이 최종일({parquet_max.date()}) 이전에 수집 중단 ({len(stale)}건) ---")
        print(stale[["no", "단지명", "매물명", "마지막수집일", "5월수집일수"]].to_string(index=False))
    nf = result[result["매칭"] == "not_found"]
    if not nf.empty:
        print(f"\n--- 미매칭 ({len(nf)}건) ---")
        print(nf.to_string(index=False))


if __name__ == "__main__":
    main()
