from collections import Counter
import time

import numpy as np
import pandas as pd
import streamlit as st

from data_fetcher import clean_realtor_name

# 엑셀 SOP: 갱신·AI 묶음 기준 (노출형태 제외, CP사 포함)
LISTING_SOP_KEYS = [
    "부동산명_정제",
    "단지명",
    "동/호수",
    "층/타입",
    "거래방식",
    "가격",
    "CP사",
]

# 광고 빈도 등급·경쟁사 분석 공통: 최근 28일(일요일 제외는 행 필터에서 처리)
ANALYSIS_WINDOW_DAYS = 28
RECENCY_WEIGHT_RECENT = 2.0
RECENCY_WEIGHT_OLD = 1.0
RECENCY_RECENT_MAX_DELTA = 3
TRAILING_STRIKE_MINUTES = 10

MAX_HOLD_HOURS = 24.0
SHORT_OR_NO_HOLD_LABEL = "상위권 미진입 또는 즉시 이탈"
MIN_WASTE_MINUTES_FOR_DETAIL = 10
MIN_PER_DAY = 24 * 60
PEAK_MINUTES = (8 * 60 + 30, 11 * 60 + 30, 19 * 60 + 30)


def _fill_series(col, default="미상") -> pd.Series:
    if col is None:
        return pd.Series([default], dtype="object")
    s = col if isinstance(col, pd.Series) else pd.Series(col)
    return s.fillna(default).astype(str).str.strip().replace({"nan": "", "None": ""}).replace("", default)


def ensure_listing_sop_columns(df: pd.DataFrame) -> pd.DataFrame:
    """SOP 7키 컬럼을 보강해 KeyError·NaN 그룹 붕괴를 방지한다."""
    if df is None:
        return pd.DataFrame()
    if df.empty:
        return df
    out = df.copy()
    if "부동산명_정제" not in out.columns:
        if "부동산명_통합" in out.columns:
            out["부동산명_정제"] = out["부동산명_통합"].astype(str)
        elif "부동산명" in out.columns:
            out["부동산명_정제"] = out["부동산명"].map(clean_realtor_name)
        else:
            out["부동산명_정제"] = "미상"
    for c in LISTING_SOP_KEYS:
        if c not in out.columns:
            out[c] = "미상"
        else:
            out[c] = _fill_series(out[c], "미상")
    return out


def _hours_excluding_daily_midnight_to_8am(t0, t1) -> float:
    """[t0, t1) 구간(시간)에서 매일 00:00~08:00(KST·naive 기준) 겹침을 제외한 시간(h)."""
    t0 = pd.Timestamp(t0)
    t1 = pd.Timestamp(t1)
    if pd.isna(t0) or pd.isna(t1) or t1 <= t0:
        return 0.0
    total_h = (t1 - t0).total_seconds() / 3600.0
    night_h = 0.0
    d = t0.normalize()
    end_d = t1.normalize()
    while d <= end_d:
        lo, hi = d, d + pd.Timedelta(hours=8)
        o0, o1 = max(t0, lo), min(t1, hi)
        if o1 > o0:
            night_h += (o1 - o0).total_seconds() / 3600.0
        d += pd.Timedelta(days=1)
    return max(0.0, total_h - night_h)


def _fmt_hm(ts):
    if ts is None:
        return "--"
    ts = pd.Timestamp(ts)
    if pd.isna(ts):
        return "--"
    return ts.strftime("%H:%M")


def parse_conf_date_str(d_str):
    """확인일자 텍스트 -> date 또는 None (예: '26.04.21', '2026-04-25')."""
    s = str(d_str).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        return None
    if len(s) == 8 and s.count(".") == 2:
        try:
            return pd.Timestamp(f"20{s.replace('.', '-')}").date()
        except (ValueError, TypeError):
            pass
    try:
        parsed = pd.to_datetime(s, errors="coerce")
        if pd.isna(parsed):
            return None
        return pd.Timestamp(parsed).date()
    except Exception:
        return None
    return None


def _confirm_date_is_sunday(val):
    d = parse_conf_date_str(val)
    return d is not None and d.weekday() == 6


def filter_exclude_sunday_rows(df, time_col, confirm_col="확인일자"):
    """수집일시 또는 확인일자(파싱 가능 시)가 일요일인 행은 분석·집계에서 제외."""
    if df is None or df.empty:
        return df
    if time_col not in df.columns:
        return df.copy()
    out = df.copy()
    ts = pd.to_datetime(out[time_col], errors="coerce")
    mask = ts.dt.weekday != 6
    if confirm_col in out.columns:
        mask &= ~out[confirm_col].apply(_confirm_date_is_sunday)
    return out.loc[mask].copy()


def _count_confirm_change_events(grp, time_col, confirm_col="확인일자"):
    if confirm_col not in grp.columns:
        return 0
    return int(grp[confirm_col].dropna().astype(str).str.strip().nunique())


def _deduped_renewal_rows_per_bundle(df, bundle_col="매물묶음키"):
    if df is None or df.empty or "수집일시" not in df.columns:
        return pd.Series(dtype="int64")
    if bundle_col not in df.columns:
        return pd.Series(dtype="int64")
    sub = df.copy()
    sub["_ts"] = pd.to_datetime(sub["수집일시"], errors="coerce")
    sub = sub[sub["_ts"].dt.weekday != 6]
    if "확인일자" in sub.columns:
        sub = sub[~sub["확인일자"].apply(_confirm_date_is_sunday)]
    dedup = [bundle_col, "_ts"]
    if "부동산명" in sub.columns:
        dedup.append("부동산명")
    if "확인일자" in sub.columns:
        dedup.append("확인일자")
    sub = sub.drop_duplicates(subset=dedup)
    return sub.groupby(bundle_col).size()


def count_renewal_events_for_bundle(
    tracked_df, b_key, bundle_col="매물묶음키", window_days=ANALYSIS_WINDOW_DAYS
):
    if tracked_df is None or tracked_df.empty:
        return 0
    if bundle_col not in tracked_df.columns or "수집일시" not in tracked_df.columns:
        return 0
    if "트랙키" not in tracked_df.columns:
        return 0
    work = tracked_df.copy()
    sub = work[work[bundle_col] == b_key]
    if sub.empty:
        return 0
    track_keys = sub["트랙키"].dropna().unique().tolist()
    if track_keys:
        sub = work[work["트랙키"].isin(track_keys)].copy()
    sub["_tw"] = pd.to_datetime(sub["수집일시"], errors="coerce")
    sub = sub.dropna(subset=["_tw"])
    if sub.empty:
        return 0
    ref = sub["_tw"].max()
    start = ref.normalize() - pd.Timedelta(days=window_days - 1)
    sub = sub[sub["_tw"] >= start]
    sub = filter_exclude_sunday_rows(sub, "_tw")
    if sub.empty or "확인일자" not in sub.columns:
        return 0
    return int(sub["확인일자"].dropna().astype(str).str.strip().nunique())


def attach_unified_realtor_name(df, source_col="부동산명", target_col="부동산명_통합"):
    out = df.copy()
    if source_col not in out.columns:
        raise ValueError(f"df에 '{source_col}' 컬럼이 없습니다.")
    out[target_col] = out[source_col].map(clean_realtor_name)
    return out


def _infer_exposure_type(df):
    if "노출형태" in df.columns:
        s = df["노출형태"].fillna("").astype(str).str.strip()
        return s.replace("", "묶음")
    if "묶음내순위" in df.columns:
        raw = df["묶음내순위"].fillna("").astype(str)
        return raw.str.contains("단독", na=False).map({True: "단독", False: "묶음"})
    return pd.Series(["묶음"] * len(df), index=df.index, dtype="object")


@st.cache_data(ttl=3600, show_spinner=False)
def build_listing_tracking_keys(df, time_col="수집일시"):
    """
    최종 시계열 트래킹 키 생성 (SOP 정렬):
    - 최종스펙키: 부동산명_통합 + 단지명 + 동/호수 + 층/타입 + 거래방식 + 가격 + CP사
      (노출형태는 키에서 제외 — 갱신 중 변경 가능)
    """
    start_t = time.time()
    print("[START] build_listing_tracking_keys")
    out = df.copy()
    if out.empty:
        print(f"[DONE] build_listing_tracking_keys empty ({time.time() - start_t:.2f}s)")
        return out
    if "부동산명_통합" not in out.columns:
        if "부동산명" not in out.columns:
            raise ValueError("df에 '부동산명_통합' 또는 '부동산명' 컬럼이 필요합니다.")
        out["부동산명_통합"] = out["부동산명"].map(clean_realtor_name)

    if time_col not in out.columns:
        raise ValueError(f"df에 '{time_col}' 컬럼이 필요합니다.")
    out[time_col] = pd.to_datetime(out[time_col], errors="coerce")

    if "CP사" not in out.columns:
        out["CP사"] = ""
    out["CP사"] = out["CP사"].fillna("").astype(str).str.strip()

    for c in ("단지명", "동/호수", "층/타입", "거래방식"):
        if c not in out.columns:
            out[c] = ""
        out[c] = out[c].fillna("").astype(str).str.strip()

    if "가격" not in out.columns:
        out["가격"] = ""
    out["_가격키"] = out["가격"].fillna("").astype(str).str.strip()

    out["노출형태"] = _infer_exposure_type(out)
    out["_가격수치"] = pd.to_numeric(out.get("가격"), errors="coerce")

    out["최종스펙키"] = (
        out["부동산명_통합"].fillna("").astype(str).str.strip()
        + " | "
        + out["단지명"].fillna("").astype(str).str.strip()
        + " | "
        + out["동/호수"].fillna("").astype(str).str.strip()
        + " | "
        + out["층/타입"].fillna("").astype(str).str.strip()
        + " | "
        + out["거래방식"].fillna("").astype(str).str.strip()
        + " | "
        + out["_가격키"]
        + " | "
        + out["CP사"].fillna("").astype(str).str.strip()
    )

    if "고유번호" not in out.columns:
        out["고유번호"] = "기록없음"
    out["고유번호"] = out["고유번호"].fillna("기록없음").astype(str).str.strip()
    out["_수집일자"] = out[time_col].dt.normalize()
    if "확인일자" in out.columns:
        out["_확인일자date"] = pd.to_datetime(out["확인일자"], errors="coerce").dt.date
        out.loc[out["_수집일자"].isna(), "_수집일자"] = pd.to_datetime(
            out.loc[out["_수집일자"].isna(), "_확인일자date"], errors="coerce"
        )

    out = out.sort_values([time_col], ascending=True)
    valid = out.dropna(subset=[time_col]).copy()
    daily_collisions = (
        valid.groupby(["최종스펙키", "_수집일자"], dropna=False)["고유번호"]
        .nunique()
        .reset_index(name="고유번호수")
    )
    collision_days = daily_collisions[daily_collisions["고유번호수"] >= 2][["최종스펙키", "_수집일자"]]
    if collision_days.empty:
        out["_충돌스펙"] = False
    else:
        out["_충돌스펙"] = out.set_index(["최종스펙키", "_수집일자"]).index.isin(
            collision_days.set_index(["최종스펙키", "_수집일자"]).index
        )
        spec_has_collision = out.groupby("최종스펙키")["_충돌스펙"].transform("max").astype(bool)
        out["_충돌스펙"] = spec_has_collision

    out["트랙키"] = out["최종스펙키"] + " || merged"
    out.loc[out["_충돌스펙"], "트랙키"] = (
        out.loc[out["_충돌스펙"], "최종스펙키"] + " || id=" + out.loc[out["_충돌스펙"], "고유번호"]
    )

    non_collision_mask = ~out["_충돌스펙"]
    if non_collision_mask.any():
        nc = out.loc[non_collision_mask].sort_values(["최종스펙키", time_col]).copy()
        nc["_이전수집일시"] = nc.groupby("최종스펙키")[time_col].shift(1)
        nc["_이전가격"] = nc.groupby("최종스펙키")["_가격수치"].shift(1)
        gap_days = (nc[time_col] - nc["_이전수집일시"]).dt.total_seconds() / 86400.0
        prev_abs = nc["_이전가격"].abs()
        rel_diff = (nc["_가격수치"] - nc["_이전가격"]).abs() / prev_abs.where(prev_abs > 0)
        price_break = nc["_가격수치"].notna() & nc["_이전가격"].notna() & (rel_diff > 0.10)
        nc["_신규트랙시작"] = nc["_이전수집일시"].isna() | (gap_days > 7.0) | price_break
        nc["_트랙세그"] = nc.groupby("최종스펙키")["_신규트랙시작"].cumsum().astype(int)
        nc["트랙키"] = nc["최종스펙키"] + " || merged_seg=" + nc["_트랙세그"].astype(str)
        out.loc[nc.index, "트랙키"] = nc["트랙키"]

    out = out.drop(
        columns=["_수집일자", "_충돌스펙", "_확인일자date", "_가격수치", "_가격키"],
        errors="ignore",
    )
    print(f"[DONE] build_listing_tracking_keys rows={len(out):,} ({time.time() - start_t:.2f}s)")
    return out


def _ad_eff_in_top_tier(_ignored_total_rank, bundle_rank):
    try:
        b = int(bundle_rank)
    except (TypeError, ValueError):
        return False
    return b <= 3


def calculate_ad_efficiency(df):
    import pandas as pd

    work = df.copy()
    time_col = "수집일시" if "수집일시" in work.columns else "수집일자"
    apt_col = "아파트명" if "아파트명" in work.columns else "단지명"

    if "부동산명_통합" not in work.columns or time_col not in work.columns:
        return {}
    if "확인일자" not in work.columns:
        return {}

    work[time_col] = pd.to_datetime(work[time_col], errors="coerce")
    work = work.dropna(subset=[time_col, "부동산명_통합", "확인일자"])
    work = filter_exclude_sunday_rows(work, time_col)

    if work.empty:
        return {}

    def _scalar(v):
        if isinstance(v, pd.Series):
            return v.iloc[0] if not v.empty else pd.NA
        return v

    data_today = work[time_col].max().normalize().date()
    yesterday_date = data_today - pd.Timedelta(days=1)

    work["확인일자_date"] = work["확인일자"].apply(parse_conf_date_str)

    group_keys = [apt_col, "부동산명_통합"]
    if "매물묶음키" in work.columns:
        group_keys.append("매물묶음키")
    group_keys.append("확인일자")

    agg = {}
    debug_count = 0

    for keys, grp in work.groupby(group_keys, sort=False):
        conf_date = grp["확인일자_date"].iloc[0]

        if pd.isna(conf_date) or conf_date != yesterday_date:
            continue

        broker = keys[1]
        if broker not in agg:
            agg[broker] = {"durations": [], "성공_횟수": 0, "실패_횟수(버려진돈)": 0}

        grp = grp.sort_values(time_col).reset_index(drop=True)
        start_ts = grp[time_col].iloc[0]
        debug_count += 1

        first_bundle = _scalar(grp.iloc[0].get("묶음내순위_숫자", pd.NA))

        if not _ad_eff_in_top_tier(None, first_bundle):
            agg[broker]["실패_횟수(버려진돈)"] += 1
            continue

        end_ts = start_ts
        for _, row in grp.iterrows():
            if _ad_eff_in_top_tier(None, _scalar(row.get("묶음내순위_숫자", pd.NA))):
                end_ts = row[time_col]
            else:
                break

        hours_raw = (end_ts - start_ts).total_seconds() / 3600.0
        hours = min(float(hours_raw), MAX_HOLD_HOURS)
        if hours >= 3.0:
            agg[broker]["성공_횟수"] += 1
            agg[broker]["durations"].append(float(hours))
        else:
            agg[broker]["실패_횟수(버려진돈)"] += 1

    print(f"어제({yesterday_date}) 감지된 갱신 건수: {debug_count}건")

    out = {}
    for broker, v in agg.items():
        durs = v["durations"]
        avg_h = float(sum(durs) / len(durs)) if durs else 0.0
        out[broker] = {
            "평균_유지시간(시간)": round(avg_h, 2),
            "성공_횟수": int(v["성공_횟수"]),
            "실패_횟수(버려진돈)": int(v["실패_횟수(버려진돈)"]),
        }
    return out


@st.cache_data(ttl=900, show_spinner=False)
def _build_cached_event_frame(work: pd.DataFrame, time_col: str) -> pd.DataFrame:
    """트랙키 기준 트리거 프레임. 확인일자 cummax는 SOP 7키 그룹으로 계산."""
    if work.empty or "트랙키" not in work.columns:
        return pd.DataFrame()
    if "확인일자" not in work.columns:
        return pd.DataFrame()

    base = work.sort_values(["트랙키", time_col]).copy()
    base["확인일자_정제"] = base["확인일자"].astype(str).str.strip()
    base["이전확인일자"] = base.groupby("트랙키")["확인일자_정제"].shift(1)
    base["확인일자_dt"] = pd.to_datetime(base["확인일자_정제"].astype(str), errors="coerce")

    base = ensure_listing_sop_columns(base)
    grp_keys = [k for k in LISTING_SOP_KEYS if k in base.columns]
    if not grp_keys:
        grp_keys = ["트랙키"]

    base["max_확인일자_dt"] = base.groupby(grp_keys, dropna=False)["확인일자_dt"].cummax()
    base["prev_max_dt"] = base.groupby(grp_keys, dropna=False)["max_확인일자_dt"].shift(1)

    base["is_trigger"] = base["이전확인일자"].isna() | (
        base["확인일자_dt"].notna()
        & (base["확인일자_dt"] == base["max_확인일자_dt"])
        & (base["max_확인일자_dt"] != base["prev_max_dt"])
    )
    base["trigger_ts"] = base[time_col].where(base["is_trigger"])
    base["event_id"] = base.groupby("트랙키")["is_trigger"].cumsum()
    return base[base["event_id"] > 0].copy()


@st.cache_data(ttl=3600, show_spinner=False)
def calculate_ad_efficiency_with_grades(df, broker_unified_filter=None, tracked_df=None, target_complexes=None):
    start_t = time.time()
    print("[START] calculate_ad_efficiency_with_grades")
    work = df.copy()
    time_col = "수집일시" if "수집일시" in work.columns else "수집일자" if "수집일자" in work.columns else None
    apt_col = "아파트명" if "아파트명" in work.columns else "단지명"
    _empty_g = {"count": 0, "waste": 0, "hold_min": None, "hold_max": None}
    empty_result = {
        "report": {"상": dict(_empty_g), "중": dict(_empty_g), "하": dict(_empty_g)},
        "waste_details": [],
        "yesterday": None,
        "listings_by_grade": {"상": [], "중": [], "하": []},
        "waste_hold_minutes": [],
    }
    if time_col is None or apt_col not in work.columns:
        print(f"[WARN] calculate_ad_efficiency_with_grades missing required columns ({time.time() - start_t:.2f}s)")
        return empty_result
    if "부동산명_통합" not in work.columns:
        if "부동산명" not in work.columns:
            print(f"[WARN] calculate_ad_efficiency_with_grades missing broker column ({time.time() - start_t:.2f}s)")
            return empty_result
        work = attach_unified_realtor_name(work)
    required = ["확인일자", "묶음내순위_숫자", "부동산명_통합"]
    if any(c not in work.columns for c in required):
        print(f"[WARN] calculate_ad_efficiency_with_grades required columns insufficient ({time.time() - start_t:.2f}s)")
        return empty_result

    if tracked_df is None:
        raise ValueError("calculate_ad_efficiency_with_grades에는 tracked_df(트래킹 완료 DF)가 필요합니다.")
    base = tracked_df
    if target_complexes and "단지명" in base.columns:
        base = base[base["단지명"].isin(target_complexes)]
        if base.empty:
            print(f"[WARN] calculate_ad_efficiency_with_grades empty after complex filter ({time.time() - start_t:.2f}s)")
            return empty_result
    if broker_unified_filter is not None and str(broker_unified_filter).strip():
        broker_norm = str(broker_unified_filter).strip()
        if "부동산명_통합" in base.columns:
            base = base[base["부동산명_통합"].astype(str) == broker_norm]
        elif "부동산명" in base.columns:
            base = base[base["부동산명"].astype(str).map(clean_realtor_name) == broker_norm]
        if base.empty:
            print(f"[WARN] calculate_ad_efficiency_with_grades empty after broker filter ({time.time() - start_t:.2f}s)")
            return empty_result

    work = base.copy()
    work[time_col] = pd.to_datetime(work[time_col], errors="coerce")
    work = work.dropna(subset=[time_col, "확인일자", "부동산명_통합"])
    work = filter_exclude_sunday_rows(work, time_col)
    if work.empty:
        print(f"[WARN] calculate_ad_efficiency_with_grades empty after cleaning ({time.time() - start_t:.2f}s)")
        return empty_result

    data_end = work[time_col].max()
    window_start = data_end.normalize() - pd.Timedelta(days=ANALYSIS_WINDOW_DAYS - 1)
    work = work[work[time_col] >= window_start].copy()
    if work.empty:
        print(f"[WARN] calculate_ad_efficiency_with_grades empty 14-day window ({time.time() - start_t:.2f}s)")
        return empty_result

    work = _build_cached_event_frame(work, time_col=time_col)
    if work.empty:
        print(f"[WARN] calculate_ad_efficiency_with_grades empty events ({time.time() - start_t:.2f}s)")
        return empty_result

    if "매물묶음키" not in work.columns:
        work["매물묶음키"] = ""
    evt = work[work["is_trigger"]][["트랙키", "event_id", "trigger_ts", "매물묶음키", "부동산명_통합"]].copy()
    evt["next_trigger_ts"] = evt.groupby("트랙키")["trigger_ts"].shift(-1)
    freq_14d = evt.groupby("트랙키")["event_id"].nunique().rename("주간_갱신횟수")
    evt = evt.join(freq_14d, on="트랙키")
    evt["grade"] = pd.cut(
        evt["주간_갱신횟수"],
        bins=[-1, 4, 9, float("inf")],
        labels=["하", "중", "상"],
    ).astype(str)

    rank_rows = work[["트랙키", "event_id", time_col, "묶음내순위_숫자"]].copy()
    rank_rows = rank_rows.join(evt.set_index(["트랙키", "event_id"])["next_trigger_ts"], on=["트랙키", "event_id"])
    rank_rows = rank_rows[rank_rows["next_trigger_ts"].isna() | (rank_rows[time_col] <= rank_rows["next_trigger_ts"])].copy()
    rank_rows["is_top3"] = pd.to_numeric(rank_rows["묶음내순위_숫자"], errors="coerce").fillna(999).astype(int) <= 3
    rank_rows["next_ts"] = rank_rows.groupby(["트랙키", "event_id"])[time_col].shift(-1)
    rank_rows["end_ts"] = rank_rows["next_ts"].where(rank_rows["next_ts"].notna(), rank_rows["next_trigger_ts"])
    rank_rows["dur_h"] = [
        _hours_excluding_daily_midnight_to_8am(a, b)
        for a, b in zip(rank_rows[time_col], rank_rows["end_ts"])
    ]
    rank_rows["dur_h"] = pd.to_numeric(rank_rows["dur_h"], errors="coerce").fillna(0.0).clip(lower=0)
    rank_rows["top3_dur_h"] = rank_rows["dur_h"].where(rank_rows["is_top3"], 0.0)
    hold_by_event = rank_rows.groupby(["트랙키", "event_id"])["top3_dur_h"].sum().rename("hold_h")
    first_top3 = rank_rows.groupby(["트랙키", "event_id"])["is_top3"].first().rename("start_in_top3")

    evt = evt.join(hold_by_event, on=["트랙키", "event_id"]).join(first_top3, on=["트랙키", "event_id"])
    evt["hold_h"] = evt["hold_h"].fillna(0.0)
    evt["start_in_top3"] = evt["start_in_top3"].fillna(False)
    evt["어제_날짜"] = evt["trigger_ts"].dt.normalize().dt.date
    yesterday = (data_end.normalize() - pd.Timedelta(days=1)).date()
    y_evt = evt[evt["어제_날짜"] == yesterday].copy()

    if y_evt.empty:
        listings = evt.sort_values(["grade", "주간_갱신횟수"], ascending=[True, False]).drop_duplicates("트랙키", keep="first")
        listings["어제_갱신_있음"] = False
        listings["어제_유지_시간"] = None
        listings["어제_성공"] = None
        listings["waste_minutes"] = 0
        listings["유지_시간_표시"] = None
    else:
        y_evt["어제_성공"] = y_evt["start_in_top3"] & (y_evt["hold_h"] >= 3.0)
        y_evt["어제_실패"] = ~y_evt["어제_성공"]
        y_evt["waste_minutes"] = (y_evt["hold_h"] * 60.0).round().astype(int).clip(lower=0)
        y_evt["유지_시간_표시"] = y_evt["trigger_ts"].dt.strftime("[%H:%M] ") + SHORT_OR_NO_HOLD_LABEL
        success_mask = y_evt["어제_성공"]
        y_evt.loc[success_mask, "유지_시간_표시"] = (
            y_evt.loc[success_mask, "trigger_ts"].dt.strftime("[%H:%M ~ ")
            + (y_evt.loc[success_mask, "trigger_ts"] + pd.to_timedelta(y_evt.loc[success_mask, "hold_h"], unit="h")).dt.strftime("%H:%M")
            + " 유지]"
        )
        fail_mask = ~success_mask & (y_evt["waste_minutes"] >= MIN_WASTE_MINUTES_FOR_DETAIL)
        y_evt.loc[fail_mask, "유지_시간_표시"] = (
            y_evt.loc[fail_mask, "trigger_ts"].dt.strftime("[%H:%M ~ ")
            + (y_evt.loc[fail_mask, "trigger_ts"] + pd.to_timedelta(y_evt.loc[fail_mask, "hold_h"], unit="h")).dt.strftime("%H:%M")
            + " 이탈]"
        )
        y_evt["어제_갱신_있음"] = True
        listings = evt.sort_values(["트랙키", "trigger_ts"]).drop_duplicates("트랙키", keep="last")[
            ["트랙키", "매물묶음키", "부동산명_통합", "주간_갱신횟수", "grade"]
        ]
        listings = listings.merge(
            y_evt[["트랙키", "어제_갱신_있음", "hold_h", "어제_성공", "waste_minutes", "유지_시간_표시"]],
            on="트랙키",
            how="left",
        )
        listings["어제_유지_시간"] = listings["hold_h"]
        listings = listings.drop(columns=["hold_h"])

    listings["어제_갱신_있음"] = listings["어제_갱신_있음"].fillna(False)
    listings["waste_minutes"] = listings["waste_minutes"].fillna(0).astype(int)

    y_for_report = listings[listings["어제_갱신_있음"]].copy()
    report_df = (
        y_for_report.groupby("grade", dropna=False)
        .agg(
            count=("어제_성공", lambda s: int((s == True).sum())),
            waste=("어제_성공", lambda s: int((s == False).sum())),
            hold_min=("어제_유지_시간", lambda s: round(float(s[s.notna() & (s >= 3.0)].min()), 2) if (s.notna() & (s >= 3.0)).any() else None),
            hold_max=("어제_유지_시간", lambda s: round(float(s[s.notna() & (s >= 3.0)].max()), 2) if (s.notna() & (s >= 3.0)).any() else None),
        )
        .reset_index()
    )
    report = {"상": dict(_empty_g), "중": dict(_empty_g), "하": dict(_empty_g)}
    for g in ["상", "중", "하"]:
        row = report_df[report_df["grade"] == g]
        if not row.empty:
            report[g] = {
                "count": int(row["count"].iloc[0]),
                "waste": int(row["waste"].iloc[0]),
                "hold_min": row["hold_min"].iloc[0],
                "hold_max": row["hold_max"].iloc[0],
            }

    y_waste = y_for_report[(y_for_report["어제_성공"] == False)].copy()
    y_waste["minutes"] = y_waste["waste_minutes"].astype(int)
    hour = y_waste["트랙키"].map(
        y_evt.set_index("트랙키")["trigger_ts"].dt.hour.to_dict() if not y_evt.empty else {}
    ).fillna(0).astype("Int64")
    ampm = hour.map(lambda h: "오전" if int(h) < 12 else "오후").astype(str)
    disp_h = hour.map(lambda h: int(h) if 1 <= int(h) <= 12 else (int(h) - 12 if int(h) > 12 else 12)).astype("Int64")
    y_waste["time_label"] = ampm.astype(str) + " " + disp_h.astype(str) + "시경"
    waste_details = (
        y_waste.sort_values("minutes", ascending=False)[["time_label", "minutes", "grade"]]
        .head(5)
        .to_dict("records")
    )
    waste_hold_minutes_all = y_waste["minutes"].tolist()

    listing_records = listings.rename(columns={"grade": "등급"})
    listing_records = listing_records.sort_values(["등급", "어제_갱신_있음", "주간_갱신횟수"], ascending=[True, False, False])
    col_list = [
        "매물묶음키",
        "트랙키",
        "부동산명_통합",
        "주간_갱신횟수",
        "어제_갱신_있음",
        "어제_유지_시간",
        "어제_성공",
        "waste_minutes",
        "유지_시간_표시",
    ]
    col_list = [c for c in col_list if c in listing_records.columns]
    listings_by_grade = {
        "상": listing_records[listing_records["등급"] == "상"][col_list].to_dict("records"),
        "중": listing_records[listing_records["등급"] == "중"][col_list].to_dict("records"),
        "하": listing_records[listing_records["등급"] == "하"][col_list].to_dict("records"),
    }

    out = {
        "report": report,
        "waste_details": waste_details,
        "yesterday": yesterday,
        "listings_by_grade": listings_by_grade,
        "waste_hold_minutes": waste_hold_minutes_all,
    }
    print(f"[DONE] calculate_ad_efficiency_with_grades events={len(evt):,} y_events={len(y_evt):,} ({time.time() - start_t:.2f}s)")
    return out


def calculate_heat_level(df):
    work = df.copy()
    required = ["매물묶음키", "수집일시", "확인일자"]
    missing = [c for c in required if c not in work.columns]
    if missing:
        raise ValueError(f"df에 필요한 컬럼이 없습니다: {missing}")

    if "부동산명_통합" not in work.columns:
        if "부동산명" not in work.columns:
            raise ValueError("df에 '부동산명_통합' 또는 '부동산명' 컬럼이 필요합니다.")
        work = attach_unified_realtor_name(work)

    work = build_listing_tracking_keys(work, time_col="수집일시")
    work["수집일시"] = pd.to_datetime(work["수집일시"], errors="coerce")
    work = work.dropna(subset=["수집일시", "트랙키", "부동산명_통합"])
    work = filter_exclude_sunday_rows(work, "수집일시")
    if work.empty:
        return {}

    work = work.sort_values(["트랙키", "수집일시"], ascending=True)
    work["이전_확인일자"] = work.groupby(["트랙키"])["확인일자"].shift(1)
    changed = (
        work["이전_확인일자"].notna()
        & work["확인일자"].notna()
        & (work["이전_확인일자"].astype(str).str.strip() != work["확인일자"].astype(str).str.strip())
    )
    renew_events = work[changed]

    day_span = (work["수집일시"].max().date() - work["수집일시"].min().date()).days + 1
    day_span = max(1, int(day_span))

    participants = work.groupby("트랙키")["부동산명_통합"].nunique()
    renew_counts = renew_events.groupby("트랙키").size() if not renew_events.empty else pd.Series(dtype="int64")
    bundle_keys = work["트랙키"].dropna().unique().tolist()

    out = {}
    for b_key in bundle_keys:
        p_cnt = int(participants.get(b_key, 0))
        r_cnt = int(renew_counts.get(b_key, 0))
        daily = float(r_cnt / day_span)

        if daily >= 3.0 or p_cnt >= 8:
            level, label = 3, "치열"
        elif daily >= 1.2 or p_cnt >= 4:
            level, label = 2, "보통"
        else:
            level, label = 1, "여유"

        out[b_key] = {
            "heat_level": level,
            "heat_label": label,
            "daily_renewals": round(daily, 2),
            "participants": p_cnt,
        }
    return out


def _circ_minute_dist(a, b, n=MIN_PER_DAY):
    a = int(a) % n
    b = int(b) % n
    d = abs(a - b)
    return min(d, n - d)


def _nearest_peak_distance(m):
    return min(_circ_minute_dist(m, p) for p in PEAK_MINUTES)


def _recommend_from_gap_and_peaks(active_minutes, active_hours):
    hour_cnt = Counter(active_hours)
    R = sorted(set(int(m) % MIN_PER_DAY for m in active_minutes))
    if not R:
        best_m = min(
            range(MIN_PER_DAY),
            key=lambda s: (_nearest_peak_distance(s), hour_cnt.get(s // 60, 0), s),
        )
        return best_m // 60, best_m % 60

    best_len = -1
    gap_starts = []
    n = len(R)
    for i in range(n):
        a = R[i]
        b = R[(i + 1) % n]
        if i < n - 1:
            gap_len = b - a - 1
            g_start = a + 1
        else:
            gap_len = MIN_PER_DAY - a + b - 1
            g_start = (a + 1) % MIN_PER_DAY
        if gap_len < 0:
            gap_len = 0
        if gap_len == 0:
            continue
        if gap_len > best_len:
            best_len = gap_len
            gap_starts = [g_start]
        elif gap_len == best_len:
            gap_starts.append(g_start)

    if not gap_starts:
        best_m = min(
            range(MIN_PER_DAY),
            key=lambda s: (_nearest_peak_distance(s), hour_cnt.get(s // 60, 0), s),
        )
        return best_m // 60, best_m % 60

    best_m = min(
        gap_starts,
        key=lambda s: (_nearest_peak_distance(s), hour_cnt.get(int(s) // 60, 0), int(s)),
    )
    return int(best_m) // 60, int(best_m) % 60


def _recency_weight_delta(delta_days: int) -> float:
    if delta_days < 0 or delta_days > 13:
        return 0.0
    if delta_days <= RECENCY_RECENT_MAX_DELTA:
        return RECENCY_WEIGHT_RECENT
    return RECENCY_WEIGHT_OLD


def _fmt_expected_duration(total_mins: int) -> str:
    h = total_mins // 60
    m = total_mins % 60
    if h <= 0:
        return f"{m}분"
    if m <= 0:
        return f"{h}시간"
    return f"{h}시간 {m}분"


# --- 다중 시간 추천 (Multi-Recommendations) 파라미터 ----------------------------
# 1·2순위 시각 최소 간격(분). 미만이면 2순위 후보에서 제외
SECONDARY_MIN_GAP_MINUTES = 180
# 2순위 점수가 1순위 대비 이 비율 미만이면 노출 생략 (너무 약한 빈집 차단)
SECONDARY_MIN_SCORE_RATIO = 0.30
# 2순위 최소 비즈니스 시간 (분). 1시간 미만 빈집은 추천 가치 낮음
SECONDARY_MIN_BUSINESS_MINS = 60

# 경쟁 과열로 모든 빈집 점수가 음수일 때 네이버 일반 피크 타임 안내 (앱 파서·마커와 호환)
NAVER_PEAK_FALLBACK_MSG = "💡 1순위: 11:30 / 2순위: 19:30"


def _format_strike_hhmm(strike_dt: pd.Timestamp) -> str:
    """추천 메시지용 시각만 (HH:MM)."""
    return f"{int(strike_dt.hour):02d}:{int(strike_dt.minute):02d}"


def _pick_top_two_strikes(
    candidates: list[tuple[float, pd.Timestamp, int]],
) -> tuple[tuple[float, pd.Timestamp, int] | None, tuple[float, pd.Timestamp, int] | None]:
    """
    (score, strike_dt, biz_mins) 후보 리스트에서
      - 1순위 : 점수 최고 (동률 시 이른 시각)
      - 2순위 : 1순위 확정 후, 시각 차이가 ≥SECONDARY_MIN_GAP_MINUTES 인 후보만 두고
                그중 점수 최고(동률 시 이른 시각). 없으면 None.
    """
    if not candidates:
        return None, None

    ordered = sorted(candidates, key=lambda x: (-x[0], x[1]))
    first = ordered[0]

    min_gap_sec = float(SECONDARY_MIN_GAP_MINUTES * 60)
    second: tuple[float, pd.Timestamp, int] | None = None
    for sc, strike, biz in ordered[1:]:
        if abs((strike - first[1]).total_seconds()) < min_gap_sec:
            continue
        if biz < SECONDARY_MIN_BUSINESS_MINS:
            continue
        if first[0] > 0 and sc / first[0] < SECONDARY_MIN_SCORE_RATIO:
            continue
        if second is None or sc > second[0] or (sc == second[0] and strike < second[1]):
            second = (sc, strike, biz)

    return first, second


def _mask_sop_match(frame: pd.DataFrame, ref: pd.Series) -> pd.Series:
    m = pd.Series(True, index=frame.index)
    for k in LISTING_SOP_KEYS:
        if k == "부동산명_정제":
            continue
        if k not in frame.columns:
            continue
        rv = str(ref.get(k, "미상")) if hasattr(ref, "get") else str(ref[k] if k in ref.index else "미상")
        m &= frame[k].astype(str) == rv
    return m


def _enemy_deadline_hour_from_comp_df(
    comp_df: pd.DataFrame | None,
    competitor_unified_names,
) -> int | None:
    """
    감시망과 동일한 comp_df에서, 해당 묶음 경쟁사(통합 부동산명)들의
    '오늘 요일 마지노선' 중 가장 늦은 시각(시)을 반환. 결측·미매칭이면 None.
    """
    if comp_df is None or comp_df.empty:
        return None
    if "부동산명" not in comp_df.columns or "오늘 요일 마지노선" not in comp_df.columns:
        return None
    name_set = {str(x).strip() for x in competitor_unified_names if str(x).strip()}
    if not name_set:
        return None
    sub = comp_df.loc[comp_df["부동산명"].astype(str).isin(name_set)]
    if sub.empty:
        return None
    dl = pd.to_numeric(sub["오늘 요일 마지노선"], errors="coerce").dropna()
    if dl.empty:
        return None
    mh = int(dl.max())
    if mh < 0 or mh > 23:
        return None
    return mh


@st.cache_data(ttl=3600)
def precalculate_ai_strategy(
    t_tracked_df,
    boosted_tracked_df,
    filter_realtor_name,
    comp_df: pd.DataFrame | None = None,
):
    """
    Gap & Peak Scoring. 경쟁사 갱신은 SOP 7키(부동산명_정제~CP사)로 내 매물 묶음과 동일한 스펙만 집계한다.
    strategy_dict 키는 app.py 호환을 위해 `매물묶음키` 유지.

    comp_df: 대시보드 감시망과 동일한 경쟁사 패턴 테이블(오늘 요일 마지노선 포함).
             None/결측이면 마지노선 이전 후보 제한을 적용하지 않는다.
    """
    strategy_dict = {}
    t_work = ensure_listing_sop_columns(t_tracked_df.copy())
    b_work = ensure_listing_sop_columns(boosted_tracked_df.copy())
    target_u = clean_realtor_name(filter_realtor_name)

    if "부동산명" not in t_work.columns:
        return strategy_dict
    t_work["_nm_u"] = t_work["부동산명"].apply(clean_realtor_name)
    vip_current = t_work[t_work["_nm_u"] == target_u].drop(columns=["_nm_u"], errors="ignore")

    if "매물묶음키" not in vip_current.columns:
        return strategy_dict

    if "부동산명" in b_work.columns:
        b_work["_nm_u"] = b_work["부동산명"].apply(clean_realtor_name)
    else:
        b_work["_nm_u"] = ""

    vip_bundles = vip_current["매물묶음키"].dropna().unique()
    # 경쟁 활동이 전혀 없을 때의 대체 메시지 (다중 추천 포맷 통일)
    no_activity_msg = "💡 1순위: 11:30 / 2순위: 19:30"

    for b_key in vip_bundles:
        vsub = vip_current[vip_current["매물묶음키"] == b_key]
        if vsub.empty:
            strategy_dict[b_key] = no_activity_msg
            continue
        if "수집일시" in vsub.columns:
            vsub = vsub.sort_values("수집일시")
        ref = vsub.iloc[-1]

        sop_mask = _mask_sop_match(b_work, ref)
        comp_mask = b_work["_nm_u"].astype(str) != target_u if "_nm_u" in b_work.columns else pd.Series(True, index=b_work.index)
        bb = b_work[sop_mask & comp_mask].copy()

        if bb.empty:
            strategy_dict[b_key] = no_activity_msg
            continue

        bb = bb.copy()
        bb["_ts"] = pd.to_datetime(bb.get("수집일시"), errors="coerce")
        bb = bb.dropna(subset=["_ts"])
        bb = bb[bb["_ts"].dt.weekday != 6]

        if "확인일자" in bb.columns:
            c_s = bb["확인일자"].astype(str).str.strip()
            c_dt = pd.to_datetime(c_s, format="%y.%m.%d", errors="coerce")
            c_na = c_dt.isna() & (c_s != "") & (c_s.str.lower() != "nan")
            if c_na.any():
                c_dt = c_dt.copy()
                c_dt.loc[c_na] = pd.to_datetime(c_s.loc[c_na], errors="coerce")
            keep = c_dt.isna() | (c_dt.dt.weekday != 6)
            bb = bb.loc[keep].copy()

        if bb.empty:
            strategy_dict[b_key] = no_activity_msg
            continue

        ref_ts = bb["_ts"].max()
        win_start = ref_ts.normalize() - pd.Timedelta(days=ANALYSIS_WINDOW_DAYS - 1)
        bb = bb[bb["_ts"] >= win_start]
        if bb.empty:
            strategy_dict[b_key] = no_activity_msg
            continue

        dedup_cols = ["_ts", "부동산명"] if "부동산명" in bb.columns else ["_ts"]
        if "확인일자" in bb.columns:
            dedup_cols.append("확인일자")
        dedup_cols = [c for c in dedup_cols if c in bb.columns]
        bb = bb.drop_duplicates(subset=dedup_cols)

        ref_date = pd.Timestamp(ref_ts).normalize().date()
        hour_weights = [0.0] * 24
        latest_ts: dict[int, pd.Timestamp] = {}

        for ts in bb["_ts"].dropna():
            ts = pd.Timestamp(ts)
            h = int(ts.hour)
            delta = (ref_date - ts.normalize().date()).days
            w = _recency_weight_delta(delta)
            if w <= 0:
                continue
            hour_weights[h] += w
            if h not in latest_ts or ts > latest_ts[h]:
                latest_ts[h] = ts

        active = sorted(latest_ts.keys())
        if not active:
            strategy_dict[b_key] = no_activity_msg
            continue

        strike_dt = {h: latest_ts[h] + pd.Timedelta(minutes=30) for h in active}

        def gap_score_and_business(start: pd.Timestamp, end: pd.Timestamp, h_cur: int) -> tuple[float, int]:
            if end <= start:
                return 0.0, 0
            total_mins = int(np.ceil((end - start).total_seconds() / 60.0))
            if total_mins <= 0:
                return 0.0, 0
            rng = pd.date_range(start, periods=total_mins, freq="min")
            hours = rng.hour.to_numpy()
            w = np.zeros(total_mins, dtype=np.float64)
            w[(hours >= 8) & (hours < 10)] = 1.5
            w[(hours >= 10) & (hours < 12)] = 1.0
            w[(hours >= 12) & (hours < 14)] = 2.0
            w[(hours >= 14) & (hours < 18)] = 1.5
            w[(hours >= 18) & (hours <= 23)] = 3.0
            raw = float(w.sum())
            biz = int((hours >= 8).sum())
            weighted = raw * (1.0 + hour_weights[h_cur] * 0.1)
            return weighted, biz

        # 모든 빈집 후보의 점수를 수집 (단일 best가 아니라 1·2순위 도출용)
        candidates: list[tuple[float, pd.Timestamp, int]] = []

        n_act = len(active)
        first_h = active[0]
        if first_h < 8:
            first_h = 9

        enemy_deadline_h: int | None = None
        if "_nm_u" in bb.columns and not bb.empty:
            enemy_deadline_h = _enemy_deadline_hour_from_comp_df(
                comp_df, bb["_nm_u"].dropna().unique()
            )

        for i in range(n_act):
            h_cur = active[i]
            start = strike_dt[h_cur]

            if start.hour < 8:
                continue

            day0 = start.normalize()
            end = day0 + pd.Timedelta(days=1) + pd.Timedelta(hours=first_h)

            sc, biz_m = gap_score_and_business(start, end, h_cur)
            if enemy_deadline_h is not None:
                deadline_minutes = int(enemy_deadline_h) * 60
                strike_minutes = int(start.hour) * 60 + int(start.minute)
                if strike_minutes < deadline_minutes:
                    continue
            candidates.append((sc, start, biz_m))

        if not candidates:
            strategy_dict[b_key] = NAVER_PEAK_FALLBACK_MSG
            continue
        if all(c[0] < 0 for c in candidates):
            strategy_dict[b_key] = NAVER_PEAK_FALLBACK_MSG
            continue

        first, second = _pick_top_two_strikes(candidates)
        if first is None:
            strategy_dict[b_key] = NAVER_PEAK_FALLBACK_MSG
            continue

        msg = f"💡 1순위: {_format_strike_hhmm(first[1])}"
        if second is not None:
            msg += f" / 2순위: {_format_strike_hhmm(second[1])}"

        strategy_dict[b_key] = msg

    return strategy_dict


@st.cache_data(max_entries=2, show_spinner=False)
def get_cached_bp_df(comp_df, b_boosted_comp, total_sessions):
    b_ranks = comp_df.groupby(["매물묶음키", "수집일시"])["묶음내순위_숫자"].min().reset_index()
    appearances = b_ranks.groupby("매물묶음키")["수집일시"].nunique()
    avg_ranks = b_ranks.groupby("매물묶음키")["묶음내순위_숫자"].mean()

    bp = pd.DataFrame(
        {
            "매물묶음키": appearances.index,
            "생존율_num": (appearances / total_sessions) * 100,
            "평균 순위": avg_ranks,
        }
    ).reset_index(drop=True)

    def get_action_plan(sr):
        if sr >= 80:
            return "🟢 S급 (집중 관리)"
        if sr >= 40:
            return "🟡 A급 (가성비 방어)"
        return "🔴 불량 (광고 중단)"

    bp["AI 추천 액션"] = bp["생존율_num"].apply(get_action_plan)
    renew_counts = _deduped_renewal_rows_per_bundle(b_boosted_comp)
    bp["갱신횟수"] = bp["매물묶음키"].map(renew_counts).fillna(0)
    return bp
