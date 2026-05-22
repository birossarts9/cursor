r"""
verify_renewal_impact.py

부동산 광고 갱신 데이터로 TOP RANK AI의 3대 B2B SaaS 가설을 검증한다.

실행 예:
  python verify_renewal_impact.py --data-dir C:\Users\biros\Desktop\cursor
  python verify_renewal_impact.py --max-rows 100000
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time
import warnings
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
os.environ.setdefault("STREAMLIT_LOG_LEVEL", "error")

try:
    from tqdm.auto import tqdm  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - tqdm 미설치 환경의 안전장치
    def tqdm(iterable: Iterable | None = None, **_: object):
        return iterable if iterable is not None else None

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from data_fetcher import clean_realtor_name, process_data  # noqa: E402


TARGET_FILES = [
    "naver_market_report_2026_05.parquet",
]

CLUSTER_GROUP_ORDER = ["소규모 (1~3)", "중규모 (4~10)", "대규모 (11+)"]
CLUSTER_LABEL_MAP = {
    "소규모 (1~3)": "소규모 단지",
    "중규모 (4~10)": "중규모 단지",
    "대규모 (11+)": "대규모 단지",
}
POSITION_ORDER = ["First", "Middle", "Last"]
TIER_ORDER = ["Top 1%", "Top 1~10%", "Top 10~30%", "기타(하위권)"]
RANK_DROPOUT = 999
MAX_OBS_INTERVAL_MIN = 60.0
H2_OBS_WINDOW_HOURS = 24  # 시간대 분포 진단용
H2_CRAWLER_BREAK_MIN = 120.0  # 갱신 후 연속 구간: 수집 간격 > 2h → 로그 단절·정전
H2_OUTPUT_CSV = "hypothesis_2_last_mover.csv"
H2_OUTPUT_COLUMNS = ["규모", "순서", "표본_수", "평균_순수_상위권_유지_시간(시간)"]


@dataclass(frozen=True)
class Survival24hMetric:
    top3_minutes: float
    corrected_win_minutes: float
    survival_hours: float
    has_observation: bool


def load_target_parquets(
    data_dir: str | os.PathLike[str] | None = None,
    max_rows: int | None = None,
) -> pd.DataFrame | None:
    """
    지정된 5월 단독 parquet을 직접 로드한다.

    기존 로직처럼 concat 후 중복 제거와 수집일시 파싱을 수행하되,
    tqdm 진행률과 디버깅용 max_rows 옵션을 제공한다.
    """
    data_dir = os.fspath(data_dir or _BASE_DIR)
    if not os.path.isdir(data_dir):
        print(f"  ! 데이터 폴더가 없습니다: {data_dir}")
        return None

    frames: list[pd.DataFrame] = []
    for name in tqdm(TARGET_FILES, desc="Parquet 로드", unit="file"):
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            print(f"  ! 파일 누락: {path}")
            continue

        t0 = time.time()
        size_mb = os.path.getsize(path) / (1024 * 1024)
        d = pd.read_parquet(path)
        print(
            f"      - {name:<38} -> {len(d):>10,} 행 "
            f"({size_mb:>7.1f} MB, {time.time() - t0:.2f}s)"
        )
        frames.append(d)

    if not frames:
        return None

    df = pd.concat(frames, ignore_index=True).drop_duplicates()
    del frames
    gc.collect()

    df["수집일시"] = pd.to_datetime(df["수집일시"], errors="coerce")
    df = df[df["수집일시"].notna()].copy()
    if max_rows is not None and max_rows > 0 and len(df) > max_rows:
        df = df.sort_values("수집일시").head(max_rows).copy()
        print(f"      -> 디버깅 max_rows 적용: {len(df):,} 행")

    if not df.empty:
        dmin, dmax = df["수집일시"].min(), df["수집일시"].max()
        days = (dmax - dmin).total_seconds() / 86400
        print(
            f"      -> 취합 결과 {len(df):,} 행 · 기간 "
            f"{dmin:%Y-%m-%d %H:%M} ~ {dmax:%Y-%m-%d %H:%M} ({days:.1f}일)"
        )
    return df


def _cluster_id(frame: pd.DataFrame) -> pd.Series:
    return frame["단지명"].astype(str).str.strip() + " | " + frame["매물묶음키"].astype(str)


def _cluster_size_group(size: pd.Series) -> pd.Categorical:
    labels = np.select(
        [size <= 3, size <= 10],
        [CLUSTER_GROUP_ORDER[0], CLUSTER_GROUP_ORDER[1]],
        default=CLUSTER_GROUP_ORDER[2],
    )
    return pd.Categorical(labels, categories=CLUSTER_GROUP_ORDER, ordered=True)


def _downcast_numeric(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["묶음내순위_숫자", "갱신_전_순위", "cluster_size", "_event_id"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce", downcast="integer")
    for col in ["_cluster_code", "_seller_cluster_key", "_track_key"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce", downcast="integer")
    return df


def _classify_renewal_position(events: pd.DataFrame) -> pd.Series:
    if events.empty:
        return pd.Series(dtype="object")

    work = events[["_event_id", "_cluster_code", "수집일시"]].copy()
    # 비즈니스 데이: 04:00 전 수집은 전날 야간 연속으로 귀속 (자정 크롤 주기 분리 방지)
    work["renewal_date"] = (work["수집일시"] - pd.Timedelta(hours=4)).dt.floor("D")
    work = work.sort_values(["_cluster_code", "renewal_date", "수집일시", "_event_id"])

    g = work.groupby(["_cluster_code", "renewal_date"], sort=False, observed=True)
    size = g["_event_id"].transform("size")
    order = g.cumcount()

    position = np.full(len(work), None, dtype=object)
    multi = size >= 5
    position[multi & (order == 0)] = "First"
    position[multi & (order == size - 1)] = "Last"
    position[multi & (order > 0) & (order < size - 1)] = "Middle"

    return pd.Series(position, index=work["_event_id"].astype(int), name="renewal_position")


def preprocess_and_segment(df: pd.DataFrame) -> pd.DataFrame:
    """
    공통 전처리:
    - 기존 process_data 로직 유지
    - 매물 묶음 규모 산정 및 cluster_size_group 생성
    - 갱신 이벤트와 First/Middle/Last 포지션 생성
    - 분석에 필요한 컬럼만 남겨 메모리 사용량 축소
    """
    print("\n[전처리] process_data 및 규모 세그먼트 생성")
    t0 = time.time()
    df = process_data(df)

    required = ["부동산명", "단지명", "매물묶음키", "고유번호", "수집일시", "묶음내순위_숫자"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"필수 컬럼 누락: {missing}")

    df = df[required].copy()
    df["수집일시"] = pd.to_datetime(df["수집일시"], errors="coerce")
    df = df[df["수집일시"].notna()].copy()
    df["묶음내순위_숫자"] = (
        pd.to_numeric(df["묶음내순위_숫자"], errors="coerce")
        .fillna(RANK_DROPOUT)
        .clip(lower=1, upper=RANK_DROPOUT)
        .astype(np.int16)
    )
    df["고유번호"] = df["고유번호"].fillna("기록없음").astype(str)
    df["부동산명_정제"] = df["부동산명"].astype(str).map(clean_realtor_name)
    df["_cluster_id"] = _cluster_id(df)

    cluster_size = (
        df.groupby("_cluster_id", sort=False, observed=True)["부동산명_정제"]
        .nunique()
        .rename("cluster_size")
    )
    df = df.merge(cluster_size, on="_cluster_id", how="left")
    df["cluster_size"] = df["cluster_size"].fillna(1).astype(np.int16)
    df["cluster_size_group"] = _cluster_size_group(df["cluster_size"])

    df["_cluster_code"] = df.groupby("_cluster_id", sort=False, observed=True).ngroup()
    df["_seller_cluster_key"] = df.groupby(
        ["_cluster_code", "부동산명_정제"], sort=False, observed=True
    ).ngroup()
    df["_track_key"] = df.groupby(
        ["_cluster_code", "부동산명_정제", "고유번호"], sort=False, observed=True
    ).ngroup()

    df = df.sort_values(["_seller_cluster_key", "수집일시"]).reset_index(drop=True)
    g = df.groupby("_seller_cluster_key", sort=False, observed=True)
    df["갱신_전_고유번호"] = g["고유번호"].shift(1)
    df["갱신_전_순위"] = g["묶음내순위_숫자"].shift(1)
    df["갱신_전_시각"] = g["수집일시"].shift(1)
    df["is_renewed"] = (
        df["갱신_전_고유번호"].notna()
        & (df["고유번호"] != df["갱신_전_고유번호"])
        & (df["고유번호"] != "기록없음")
        & (df["갱신_전_고유번호"] != "기록없음")
    )
    df["_event_id"] = -1
    df.loc[df["is_renewed"], "_event_id"] = np.arange(int(df["is_renewed"].sum()))

    events = df.loc[df["is_renewed"], ["_event_id", "_cluster_code", "수집일시"]]
    position = _classify_renewal_position(events)
    df["renewal_position"] = pd.NA
    if not position.empty:
        mask = df["is_renewed"]
        df.loc[mask, "renewal_position"] = (
            df.loc[mask, "_event_id"].astype(int).map(position)
        )
    df["renewal_position"] = pd.Categorical(
        df["renewal_position"], categories=POSITION_ORDER, ordered=True
    )

    df["부동산명_정제"] = df["부동산명_정제"].astype("category")
    df["고유번호"] = df["고유번호"].astype("category")
    df = df.drop(columns=["부동산명", "단지명", "매물묶음키", "_cluster_id"])
    df = _downcast_numeric(df)
    gc.collect()

    mem_mb = df.memory_usage(deep=True).sum() / (1024 * 1024)
    print(
        f"      -> 전처리 완료 {len(df):,} 행 · 갱신 이벤트 {int(df['is_renewed'].sum()):,} 건 "
        f"· 메모리 {mem_mb:.1f} MB · {time.time() - t0:.2f}s"
    )
    return df


def _events(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[df["is_renewed"]].copy()


def _format_minutes_to_hhmm(hour_float: float) -> str:
    if pd.isna(hour_float):
        return "-"
    minutes = int(round(float(hour_float) * 60)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _first_top3_indexing_minutes(df: pd.DataFrame, events: pd.DataFrame) -> pd.Series:
    if events.empty:
        return pd.Series(dtype="float64")

    top3_obs = df.loc[
        df["묶음내순위_숫자"] <= 3,
        ["_track_key", "수집일시"],
    ].copy()
    if top3_obs.empty:
        return pd.Series(np.nan, index=events.index, dtype="float64")

    left = events[["_event_id", "_track_key", "수집일시"]].copy()
    left = left.rename(columns={"수집일시": "갱신시각"}).sort_values("갱신시각")
    right = top3_obs.rename(columns={"수집일시": "첫_TOP3_시각"}).sort_values("첫_TOP3_시각")

    matched = pd.merge_asof(
        left,
        right,
        left_on="갱신시각",
        right_on="첫_TOP3_시각",
        by="_track_key",
        direction="forward",
        allow_exact_matches=True,
    )
    minutes = (matched["첫_TOP3_시각"] - matched["갱신시각"]).dt.total_seconds() / 60.0
    return pd.Series(minutes.values, index=matched["_event_id"].astype(int), dtype="float64")


def prove_hypothesis_1_indexing_time(df: pd.DataFrame) -> pd.DataFrame:
    print("\n[가설 1] 갱신 효과 및 인덱싱 소요 시간")
    events = _events(df)
    if events.empty:
        return pd.DataFrame()

    indexing_min = _first_top3_indexing_minutes(df, events)
    events["indexing_minutes"] = events["_event_id"].astype(int).map(indexing_min)

    before = pd.to_numeric(events["갱신_전_순위"], errors="coerce")
    after = pd.to_numeric(events["묶음내순위_숫자"], errors="coerce")
    valid_rank = before.notna() & after.notna() & (before < RANK_DROPOUT) & (after < RANK_DROPOUT)
    events["rank_lift"] = np.where(valid_rank, before - after, np.nan)
    events["top3_success"] = after <= 3

    result = (
        events.groupby("cluster_size_group", observed=True)
        .agg(
            관측건수=("_event_id", "size"),
            평균_순위상승폭=("rank_lift", "mean"),
            TOP3_도달률_pct=("top3_success", lambda s: float(s.mean() * 100)),
            평균_인덱싱_분=("indexing_minutes", "mean"),
            인덱싱_관측건수=("indexing_minutes", "count"),
        )
        .reset_index()
        .rename(columns={"cluster_size_group": "규모"})
    )
    return result.round(
        {"평균_순위상승폭": 2, "TOP3_도달률_pct": 2, "평균_인덱싱_분": 1}
    )


def _build_observation_index(df: pd.DataFrame) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    obs = df[["_track_key", "수집일시", "묶음내순위_숫자"]].sort_values(
        ["_track_key", "수집일시"]
    )
    index: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    grouped = obs.groupby("_track_key", sort=False, observed=True)
    for key, grp in tqdm(grouped, total=grouped.ngroups, desc="관측 인덱스", unit="track"):
        index[int(key)] = (
            grp["수집일시"].values.astype("datetime64[ns]"),
            grp["묶음내순위_숫자"].to_numpy(dtype=np.int16, copy=False),
        )
    return index


def _survival_24h_metric_for_event(
    times: np.ndarray,
    ranks: np.ndarray,
    event_time: pd.Timestamp,
) -> Survival24hMetric:
    """
    갱신 직후(t0)부터 TOP3 최초 이탈 또는 수집 로그 단절(>120분) 전까지의
    순수 연속 유지 시간만 합산. 과거 시점·24h 고정 창 혼입 없음.
    """
    t0 = pd.Timestamp(event_time)
    t0_ns = np.datetime64(t0, "ns")

    if len(times) == 0:
        return Survival24hMetric(0.0, 0.0, np.nan, False)

    start_idx = int(np.searchsorted(times, t0_ns, side="left"))
    if start_idx >= len(times):
        return Survival24hMetric(0.0, 0.0, np.nan, False)

    has_obs = True
    top3_min = 0.0
    win_min = 0.0

    if int(ranks[start_idx]) > 3:
        return Survival24hMetric(0.0, 0.0, 0.0, has_obs)

    for idx in range(start_idx, len(times) - 1):
        S = times[idx]
        if int(ranks[idx]) > 3:
            break

        E = times[idx + 1]
        gap_min = float((E - S) / np.timedelta64(1, "m"))
        if gap_min > H2_CRAWLER_BREAK_MIN:
            break

        seg_start = max(S, t0_ns) if idx == start_idx else S
        seg_end = E
        if seg_end <= seg_start:
            continue

        dur_min = float((seg_end - seg_start) / np.timedelta64(1, "m"))
        if dur_min <= 0:
            continue

        top3_min += dur_min
        win_min += dur_min

    if win_min <= 0:
        return Survival24hMetric(top3_min, win_min, np.nan, has_obs)

    survival_h = round(top3_min / 60.0, 1)
    return Survival24hMetric(top3_min, win_min, survival_h, has_obs)


def _append_24h_survival_metrics(df: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        events = events.copy()
        events["survival_hours_24h"] = []
        events["has_24h_observation"] = []
        return events

    obs_index = _build_observation_index(df)

    survival_hours: list[float] = []
    has_observation: list[bool] = []

    iterator = events[["_track_key", "수집일시"]].itertuples(index=False, name=None)
    for track_key, event_time in tqdm(
        iterator, total=len(events), desc="24h 생존시간 연산", unit="event"
    ):
        times, ranks = obs_index.get(
            int(track_key),
            (np.array([], dtype="datetime64[ns]"), np.array([], dtype=np.int16)),
        )
        metric = _survival_24h_metric_for_event(
            times, ranks, pd.Timestamp(event_time)
        )
        survival_hours.append(metric.survival_hours)
        has_observation.append(metric.has_observation)

    events = events.copy()
    events["survival_hours_24h"] = survival_hours
    events["has_24h_observation"] = has_observation
    return events


def _prepare_hypothesis_2_events(df: pd.DataFrame) -> pd.DataFrame:
    """가설 2: 갱신 포지션이 있는 이벤트 + 24h 생존 지표 부착."""
    events = _events(df)
    events = events[events["renewal_position"].notna()].copy()
    if events.empty:
        return events
    return _append_24h_survival_metrics(df, events)


def _top3_snapshot_hours_in_24h_window(
    times: np.ndarray,
    ranks: np.ndarray,
    event_time: pd.Timestamp,
) -> list[int]:
    """[t0, t0+24h] 내 ranks<=3 인 수집 시각의 시(0~23) 목록."""
    if len(times) == 0:
        return []
    t0_ns = np.datetime64(pd.Timestamp(event_time), "ns")
    t1_ns = t0_ns + np.timedelta64(H2_OBS_WINDOW_HOURS, "h")
    left = int(np.searchsorted(times, t0_ns, side="left"))
    right = int(np.searchsorted(times, t1_ns, side="right"))
    if right <= left:
        return []
    seg_times = times[left:right]
    seg_ranks = ranks[left:right]
    top3_mask = seg_ranks <= 3
    if not np.any(top3_mask):
        return []
    return [int(pd.Timestamp(t).hour) for t in seg_times[top3_mask]]


def print_hourly_exposure_distribution(df: pd.DataFrame, events: pd.DataFrame) -> None:
    """
    First / Last 갱신 매물의 24h 창 내 TOP3 노출 스냅샷 시각 분포(0~23시).
    가설 2 유효 관측 이벤트(events) 기준.
    """
    print(f"\n{'=' * 88}")
    print("[진단] First vs Last · 24h TOP3 노출 시간대 분포 (스냅샷 횟수)")
    print("=" * 88)

    if events is None or events.empty:
        print("  유효 이벤트 없음")
        return

    work = events.loc[events["renewal_position"].isin(["First", "Last"])].copy()
    if "has_24h_observation" in work.columns:
        work = work[work["has_24h_observation"]].copy()
    if "survival_hours_24h" in work.columns:
        work = work[work["survival_hours_24h"].notna()].copy()

    if work.empty:
        print("  First/Last 유효 관측 없음")
        return

    obs_index = _build_observation_index(df)
    hour_rows: list[dict[str, object]] = []

    iterator = zip(
        work["_track_key"].astype(int),
        work["수집일시"],
        work["renewal_position"].astype(str),
    )
    for track_key, event_time_raw, position in tqdm(
        iterator, total=len(work), desc="TOP3 시간대 집계", unit="event"
    ):
        event_time = pd.Timestamp(event_time_raw)
        times, ranks = obs_index.get(
            track_key,
            (np.array([], dtype="datetime64[ns]"), np.array([], dtype=np.int16)),
        )
        for hour in _top3_snapshot_hours_in_24h_window(times, ranks, event_time):
            hour_rows.append({"순서": position, "hour": hour})

    if not hour_rows:
        print("  TOP3 스냅샷 없음")
        return

    hour_df = pd.DataFrame(hour_rows)
    pivot = (
        hour_df.groupby(["순서", "hour"], observed=True)
        .size()
        .unstack(fill_value=0)
    )
    pivot = pivot.reindex(index=["First", "Last"], columns=range(24), fill_value=0)
    pivot.columns = [f"{h:02d}시" for h in pivot.columns]
    pivot.index.name = "순서"

    total_first = int(pivot.loc["First"].sum()) if "First" in pivot.index else 0
    total_last = int(pivot.loc["Last"].sum()) if "Last" in pivot.index else 0
    print(f"  TOP3 스냅샷 합계 — First: {total_first:,}회 | Last: {total_last:,}회")
    print(f"  (이벤트 {len(work):,}건 · 갱신 후 24h 창 내 ranks≤3 수집 시각 기준)\n")
    print(pivot.to_string())


def prove_hypothesis_2_last_mover(
    df: pd.DataFrame,
    events: pd.DataFrame | None = None,
) -> pd.DataFrame:
    print("\n[가설 2] 갱신 직후 순수 연속 TOP3 유지 시간 (Last Mover)")
    if events is None:
        events = _prepare_hypothesis_2_events(df)
    else:
        events = events.loc[events["renewal_position"].notna()].copy()
    if events.empty:
        return pd.DataFrame(columns=H2_OUTPUT_COLUMNS)
    valid = events[events["has_24h_observation"] & events["survival_hours_24h"].notna()]
    if valid.empty:
        return pd.DataFrame(columns=H2_OUTPUT_COLUMNS)

    result = (
        valid.groupby(["cluster_size_group", "renewal_position"], observed=True)
        .agg(
            표본_수=("_event_id", "size"),
            평균_순수_상위권_유지_시간_시간=("survival_hours_24h", "mean"),
        )
        .reset_index()
        .rename(
            columns={
                "cluster_size_group": "규모",
                "renewal_position": "순서",
                "평균_순수_상위권_유지_시간_시간": "평균_순수_상위권_유지_시간(시간)",
            }
        )
    )
    result["규모"] = result["규모"].astype(str).map(CLUSTER_LABEL_MAP).fillna(result["규모"])
    result["평균_순수_상위권_유지_시간(시간)"] = result["평균_순수_상위권_유지_시간(시간)"].round(1)
    return result[H2_OUTPUT_COLUMNS]


def _assign_realtor_tiers(top3_minutes: pd.Series) -> pd.Series:
    ranked = top3_minutes.sort_values(ascending=False)
    n = len(ranked)
    if n == 0:
        return pd.Series(dtype="object")

    pct = pd.Series(np.arange(1, n + 1) / n, index=ranked.index)
    labels = np.select(
        [pct <= 0.01, pct <= 0.10, pct <= 0.30],
        [TIER_ORDER[0], TIER_ORDER[1], TIER_ORDER[2]],
        default=TIER_ORDER[3],
    )
    return pd.Series(labels, index=ranked.index, name="realtor_tier")


def _top3_minutes_by_realtor(df: pd.DataFrame) -> pd.Series:
    work = df[["_track_key", "부동산명_정제", "수집일시", "묶음내순위_숫자"]].sort_values(
        ["_track_key", "수집일시"]
    )
    next_time = work.groupby("_track_key", sort=False, observed=True)["수집일시"].shift(-1)
    duration = (next_time - work["수집일시"]).dt.total_seconds().div(60.0)
    duration = duration.clip(lower=0, upper=MAX_OBS_INTERVAL_MIN).fillna(0.0)
    work["top3_minutes"] = np.where(work["묶음내순위_숫자"] <= 3, duration, 0.0)
    return work.groupby("부동산명_정제", sort=False, observed=True)["top3_minutes"].sum()


def prove_hypothesis_3_top_tier_patterns(df: pd.DataFrame) -> pd.DataFrame:
    print("\n[가설 3] 상위권 장기 집권 부동산의 행동 패턴")
    realtor_top3 = _top3_minutes_by_realtor(df)
    tiers = _assign_realtor_tiers(realtor_top3)
    if tiers.empty:
        return pd.DataFrame()

    events = _events(df)
    if events.empty:
        return pd.DataFrame()

    events = events.copy()
    events["realtor_tier"] = events["부동산명_정제"].map(tiers).astype("object")
    events["realtor_tier"] = pd.Categorical(events["realtor_tier"], categories=TIER_ORDER, ordered=True)
    events["renewal_hour"] = (
        events["수집일시"].dt.hour
        + events["수집일시"].dt.minute / 60.0
        + events["수집일시"].dt.second / 3600.0
    )
    events["renewal_date"] = events["수집일시"].dt.floor("D")

    daily = (
        events.groupby(
            ["cluster_size_group", "realtor_tier", "부동산명_정제", "renewal_date"],
            observed=True,
        )["_event_id"]
        .count()
        .rename("daily_renewals")
        .reset_index()
    )
    daily_avg = (
        daily.groupby(["cluster_size_group", "realtor_tier"], observed=True)["daily_renewals"]
        .mean()
        .rename("일평균_갱신빈도")
    )

    grouped = events.groupby(["cluster_size_group", "realtor_tier"], observed=True)
    result = grouped.agg(
        관측건수=("_event_id", "size"),
        평균_갱신시각_raw=("renewal_hour", "mean"),
        Last_Mover_비율_pct=("renewal_position", lambda s: float((s == "Last").sum() / s.notna().sum() * 100) if s.notna().sum() else np.nan),
    )
    result = result.join(daily_avg, how="left").reset_index()
    result["평균_갱신시각"] = result["평균_갱신시각_raw"].map(_format_minutes_to_hhmm)
    result = result.drop(columns=["평균_갱신시각_raw"])
    result = result.rename(columns={"cluster_size_group": "규모", "realtor_tier": "부동산티어"})
    result = result[
        ["규모", "부동산티어", "관측건수", "평균_갱신시각", "일평균_갱신빈도", "Last_Mover_비율_pct"]
    ]
    return result.round({"일평균_갱신빈도": 2, "Last_Mover_비율_pct": 2})


def _print_table(title: str, df: pd.DataFrame) -> None:
    print(f"\n{'=' * 88}")
    print(title)
    print("=" * 88)
    if df.empty:
        print("결과 없음")
    else:
        print(df.to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="5월 단독 네이버 부동산 parquet 전수 분석으로 3대 비즈니스 가설을 검증합니다."
    )
    parser.add_argument(
        "--data-dir",
        default=_BASE_DIR,
        help="naver_market_report_2026_05.parquet 파일이 있는 폴더",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="디버깅용 최대 행 수. 지정하지 않으면 전체 데이터를 분석합니다.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    total_t0 = time.time()

    print("TOP RANK AI · 3대 비즈니스 가설 검증")
    print(f"데이터 폴더: {os.path.abspath(args.data_dir)}")
    print(f"max_rows: {args.max_rows if args.max_rows else '전체'}")

    raw = load_target_parquets(args.data_dir, args.max_rows)
    if raw is None or raw.empty:
        print("  ! 분석할 데이터를 찾지 못했습니다.")
        return 1

    df = preprocess_and_segment(raw)
    del raw
    gc.collect()

    h1 = prove_hypothesis_1_indexing_time(df)
    h2_events = _prepare_hypothesis_2_events(df)
    h2 = prove_hypothesis_2_last_mover(df, events=h2_events)
    print_hourly_exposure_distribution(df, h2_events)
    h3 = prove_hypothesis_3_top_tier_patterns(df)

    _print_table("[가설 1] 규모별 갱신 효과 및 인덱싱 소요 시간", h1)
    _print_table("[가설 2] 규모별 First vs Middle vs Last · 순수 연속 TOP3 유지 시간", h2)
    _print_table("[가설 3] 규모·티어별 장기 상위권 부동산 행동 패턴", h3)

    if not h2.empty:
        csv_path = os.path.join(os.path.abspath(args.data_dir), H2_OUTPUT_CSV)
        h2.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\n[가설 2] CSV 저장: {csv_path}")

    print(f"\n총 소요 시간: {time.time() - total_t0:.2f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
