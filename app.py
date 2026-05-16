"""
24시간·당일 기준 타임라인(Plotly 간트) 대시보드.
프라임 action_df / merge_asof 엔진은 app.py 마스터 대시보드와 동일합니다.
실행: streamlit run app_map_2.py
"""
from __future__ import annotations

import base64
import csv
import html
import os
import time

import re
from datetime import datetime, timedelta, timezone

import datetime as _std_datetime_module  # log_user_action용 표준 datetime 모듈

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from data_fetcher import (
    DATA_DIR,
    clean_realtor_name,
    load_realtor_map,
    load_server_data,
    normalize_dong_ho,
    process_data,
)
from ranking_logic import (
    _hours_excluding_daily_midnight_to_8am,
    build_listing_tracking_keys,
    filter_exclude_sunday_rows,
    precalculate_ai_strategy,
)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = _APP_DIR


def log_user_action(action_detail: str) -> None:
    """사용자의 행동을 activity_logs.csv에 기록하는 함수"""
    client_id = st.query_params.get("id", "직접접속")
    log_file = os.path.join(BASE_DIR, "activity_logs.csv")
    now_str = _std_datetime_module.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not os.path.exists(log_file):
        with open(log_file, mode="w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["시간", "접속ID", "행동구분"])

    with open(log_file, mode="a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([now_str, client_id, action_detail])


# 엑셀 열 인덱스(0-based): N열 = CP사 — 멀티 CP 광고 핑퐁 노이즈 방지용 식별자
COL_CP = 13

_GUIDE_REPLY_TIME = (
    "최근 28일(4주) 치의 타사 활동 데이터를 분석합니다. 특히 최근 활동에 가중치를 주어 최신 트렌드를 반영합니다. "
    "경쟁사가 갱신을 멈추는 '빈집' 구간을 찾고, 그 구간이 점심(11~13시)이나 저녁(19~21시) 같은 "
    "피크 타임을 얼마나 길게 독점할 수 있는지 계산하여 가장 효율이 높은 타격 시간을 추천합니다."
)
_GUIDE_REPLY_SCORE = (
    "현재 노출 범위(48시간) 내에서 심야 시간을 제외한 실 영업시간 중 대표님의 매물이 "
    "1~3위 상위권(방호 성공)을 안전하게 지켜낸 시간의 비율입니다. "
    "타임라인의 파란색 막대가 촘촘하고 길수록 점수가 100점에 가까워지며, "
    "경쟁사 진입으로 밀려난 시간만큼 점수가 차감됩니다."
)
_GUIDE_REPLY_NIGHT = (
    "네이버 부동산 방문객이 거의 없는 **00시부터 08시까지의 심야 시간**은 노출되어도 효과가 없기 때문에 "
    "점수 계산과 예상 노출 시간에서 **완전히 제외(0시간 처리)**합니다. 오직 진짜 영업시간에만 집중합니다."
)

_CUSTOMER_WHITEPAPER_MD = """
📘 탑랭크 AI 핵심 활용 백서
💡 본 시스템은 네이버 부동산 데이터를 크롤링하여 분석하므로, 상세 동/호수 대신 네이버 부동산에 기재된 스펙(동, 층수, 면적, 가격, 방향)으로 매물을 식별합니다. 

🔍 기본 사용법
단지 선택: 화면 왼쪽 상단에서 분석하고자 하는 아파트 단지명을 선택할 수 있습니다.

🎯 광고 전술판 실전 활용법
상세보기: 매물 하단의 상세보기를 누르면 경쟁사들이 몇 시 몇 분에 광고를 했는지 파악할 수 있습니다.
⏳ 대기 권장: 경쟁사들이 아직 오늘 자 광고 갱신을 시작하지 않은 상태입니다. 이때 먼저 광고를 올리면 나중에 밀릴 수 있으므로, 상태가 '지금 광고'로 바뀔 때까지 대기해야 합니다.
🚀 지금 광고: 경쟁사들이 오늘 광고 세팅을 모두 완료한 최적의 타이밍입니다. 지금 즉시 광고를 올리면 상위권을 가장 오랜 시간 독점할 수 있습니다.

📌 탭별 기능 안내
📊 점유율 타임라인: 최근 48시간 동안 내 매물이 상위권을 지킨 구간(파란색)과 경쟁사에 밀린 구간(회색)을 가로 막대로 시각화하여, 내가 밀린 시점에 어떤 경쟁업체가 1위를 차지했는지 추적합니다.
📈 일간 점수 트렌드: 매일 우리 매물이 상위권을 얼마나 잘 방어했는지 일자별 점수 추이를 그래프로 보여주어 2주간의 광고 효율의 흐름을 파악합니다.
🍩 단지 내 시장 점유율: 해당 단지 전체 광고 지분 중 우리 부동산과 경쟁사들이 각각 몇 %의 점유율을 나누어 먹고 있는지 한눈에 비교합니다.

---

### 🚀 AWS 라이트세일 서버 Push 과정 리마인드

로컬 컴퓨터에서 수정을 마치고 대시보드가 스냅처럼 빨라진 것을 확인하셨다면, 아래 단계로 서버에 배포하시면 됩니다.

#### 1단계: 로컬(데스크톱 / Cursor) 터미널
```powershell
# 1. 수정된 모든 변경사항 무대 위로 올리기
git add .

# 2. 어떤 작업을 했는지 명시하여 커밋 생성
git commit -m "ui: optimize ai response speed and update user guidebook"

# 3. 깃허브 원격 저장소로 강제 밀어내기 (안전하게 내 코드로 고정)
git push origin main --force
```
"""


def _guide_md_fragments_to_html(text: str) -> str:
    """`**굵게**`만 허용하고 나머지는 이스케이프."""
    parts = re.split(r"(\*\*.+?\*\*)", text)
    out: list[str] = []
    for p in parts:
        if len(p) >= 4 and p.startswith("**") and p.endswith("**"):
            out.append("<strong>" + html.escape(p[2:-2]) + "</strong>")
        else:
            out.append(html.escape(p))
    return "".join(out).replace("\n", "<br/>")


def _extract_area_key(floor_type_text):
    s = str(floor_type_text or "")
    m = re.search(r"(\d+[A-Z]?)\s*/\s*(\d+[A-Z]?)(m²|m2)?", s)
    if m:
        suffix = m.group(3) if m.group(3) else ""
        return f"{m.group(1)}/{m.group(2)}{suffix}"
    return s.strip()


def _fmt_minutes_as_hm(minutes):
    m = max(0, int(round(float(minutes or 0))))
    h = m // 60
    r = m % 60
    return f"{h}시간 {r}분"


_RE_TRACKER_REALTOR_NOISE = re.compile(
    r"공인중개사사무소|공인중개사|부동산중개|사무소|부동산"
)


def _strip_realtor_label_noise(display_name: str) -> str:
    """카드 등 표시용: 중개사무소·공인중개사 등 접미어를 제거해 브랜드만 남김."""
    s = str(display_name or "").strip()
    if not s:
        return s
    cleaned = _RE_TRACKER_REALTOR_NOISE.sub("", s).strip()
    return cleaned if cleaned else s


def _last_renewal_hhmm_today(
    r_uni: str,
    kst_today,
    b_df: pd.DataFrame | None,
    sub_df: pd.DataFrame,
) -> str:
    """당일 해당 통합 부동산명의 마지막 수집 시각 → HH:MM (없으면 --:--)."""
    ts = None
    if b_df is not None and not b_df.empty and "부동산명_정제" in b_df.columns:
        br = b_df[b_df["부동산명_정제"] == r_uni]
        if not br.empty and "수집일시" in br.columns:
            br_dt = pd.to_datetime(br["수집일시"], errors="coerce")
            m = br_dt.dt.date == kst_today
            if m.any():
                ts = br_dt.loc[m].max()
    if ts is None or pd.isna(ts):
        if (
            not sub_df.empty
            and "부동산명_통합" in sub_df.columns
            and "수집일시" in sub_df.columns
        ):
            sr = sub_df[sub_df["부동산명_통합"] == r_uni]
            if not sr.empty:
                sr_dt = pd.to_datetime(sr["수집일시"], errors="coerce")
                m2 = sr_dt.dt.date == kst_today
                if m2.any():
                    ts = sr_dt.loc[m2].max()
    if ts is not None and pd.notna(ts):
        return pd.Timestamp(ts).strftime("%H:%M")
    return "--:--"


def _dedup_floor_type_text(text):
    raw_parts = [p.strip() for p in str(text or "").split("|") if p.strip()]
    if not raw_parts:
        return ""
    cleaned_parts = []
    prev_norm = None
    for part in raw_parts:
        norm = re.sub(r"\s+", "", part).replace("층", "")
        if prev_norm is not None and norm == prev_norm:
            continue
        cleaned_parts.append(part)
        prev_norm = norm
    return " | ".join(cleaned_parts)


def _fmt_price_kr(value) -> str:
    """원 단위 정수(또는 숫자 문자열) → '10억' / '9억 8,000' 형식. 가격 누락·비숫자는 빈 문자열."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = int(round(float(value)))
    else:
        s = str(value or "").strip()
        if not s or s.lower() in {"nan", "none", "nat"}:
            return ""
        s2 = s.replace(",", "")
        if re.fullmatch(r"-?\d+(\.\d+)?(e[+-]?\d+)?", s2, re.IGNORECASE):
            try:
                n = int(round(float(s2)))
            except (TypeError, ValueError, OverflowError):
                return s
        else:
            digits = re.sub(r"[^0-9]", "", s)
            if not digits:
                return s
            n = int(digits)
    eok = n // 100_000_000
    man = (n % 100_000_000) // 10_000
    if eok > 0 and man > 0:
        return f"{eok}억 {man:,}"
    if eok > 0:
        return f"{eok}억"
    return f"{man:,}" if man else ""


def _scalar_price_str(pr) -> str:
    """가격 컬럼 스칼라 → 라벨용 문자열 (결측·NA 안전)."""
    try:
        if pr is None or pd.isna(pr):
            return ""
    except (ValueError, TypeError):
        pass
    s = str(pr).strip()
    return "" if s.lower() in {"nan", "none", "nat"} else s


def _empty_action_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "단지명",
            "Task",
            "매물 중요도",
            "매물명",
            "광고 갱신 횟수",
            "상위권 유지 기간",
            "최종 효력 유지 시각",
            "최근 갱신 시각",
            "상태",
            "Value / Waste 횟수",
            "Waste 횟수",
            "hold_minutes_raw",
            "광고 추천 시간",
        ]
    )


def _strip_danji_from_dongho(danji: str, dongho: str) -> str:
    """동/호수 앞에 단지명이 한 번 더 붙은 경우 제거 (원본 포맷 불균일 대응)."""
    d = str(danji or "").strip()
    h = normalize_dong_ho(dongho, d)
    if not h or str(h).lower() == "nan":
        return ""
    h = str(h).strip()
    while d and h.startswith(d):
        h = h[len(d) :].lstrip(" -_/")
    return re.sub(r"\s+", " ", h).strip()


def _area_floor_compact_label(floor_type_text: str) -> str:
    """층/타입에서 `면적·층수` 한 줄 (예: 113B·15/29층). 파이프 구분 우선."""
    ft_raw = _dedup_floor_type_text(floor_type_text)
    ft = str(ft_raw or "").strip()
    if not ft or ft.lower() == "nan":
        return ""
    if "|" in ft:
        parts = [p.strip() for p in ft.split("|") if p.strip()]
        area = parts[0] if parts else ""
        floor_txt = ""
        for p in parts[1:]:
            if "층" in p or re.search(r"\d+\s*/\s*\d+", p):
                floor_txt = p
                break
        if not floor_txt and len(parts) >= 2:
            floor_txt = parts[1]
        if area and floor_txt:
            return f"{area}·{floor_txt}"
        return area or floor_txt or ""

    m_floor = re.search(r"(\d+\s*저?\s*/\s*\d+(?:\s*층)?|[저중고]/\d+\s*층|\d+\s*/\s*\d+\s*층)", ft)
    fl = m_floor.group(1).replace(" ", "") if m_floor else ""
    ar = _extract_area_key(ft)
    if ar and fl:
        return f"{ar}·{fl}"
    if ar:
        return ar
    if fl:
        return fl
    return ft


def _extract_direction_text(*values) -> str:
    """방향 컬럼이 비어 있거나 없을 때 층/타입 텍스트에서 방향을 추출."""
    direction_re = re.compile(r"(남동향|남서향|북동향|북서향|동향|서향|남향|북향)")
    for value in values:
        s = str(value or "").strip()
        if not s or s.lower() in {"nan", "none", "nat", "미상"}:
            continue
        m = direction_re.search(s)
        if m:
            return m.group(1)
    return ""


def _task_label_from_spec(
    danji: str, dongho: str, floor_type: str, price=None, direction: str = ""
) -> str:
    """타임라인·액션표 공통: `동/호수 (면적·층수 | 가격) 방향`."""
    dong_c = _strip_danji_from_dongho(danji, dongho)
    if not dong_c:
        dong_c = "—"
    mid = _area_floor_compact_label(floor_type)
    if not mid:
        mid = "—"
    ptxt = _fmt_price_kr(price)
    if not ptxt:
        ptxt = "—"
    direction_txt = str(direction or "").strip()
    if not direction_txt:
        direction_txt = _extract_direction_text(floor_type)
    if not direction_txt:
        direction_txt = "—"
    return f"{dong_c} ({mid} | {ptxt}) {direction_txt}"


def _ensure_direction_and_tracking_key(df: pd.DataFrame) -> pd.DataFrame:
    """방향을 명시 컬럼으로 표준화하고 매물묶음키 끝에 포함해 병합 충돌을 방지."""
    out = df.copy()
    if "방향" not in out.columns:
        out["방향"] = ""
    out["방향"] = out["방향"].fillna("").astype(str).str.strip()
    if "층/타입" in out.columns:
        extracted = out["층/타입"].map(_extract_direction_text)
        out["방향"] = out["방향"].where(out["방향"].astype(bool), extracted)
    out["방향"] = out["방향"].fillna("").astype(str).str.strip()

    if "매물묶음키" in out.columns:
        bkey = out["매물묶음키"].fillna("").astype(str).str.strip()
        direction = out["방향"].fillna("").astype(str).str.strip()
        has_direction = pd.Series(
            [bool(d) and d in str(k) for k, d in zip(bkey, direction)],
            index=out.index,
        )
        needs_direction = direction.astype(bool) & ~has_direction
        out.loc[needs_direction, "매물묶음키"] = (
            bkey.loc[needs_direction] + " | " + direction.loc[needs_direction]
        )
    return out


def _drop_standalone_listings(df: pd.DataFrame) -> pd.DataFrame:
    """경쟁 부동산이 없는 단독 매물을 타임라인/분석 전체에서 제외."""
    if df.empty or "매물묶음키" not in df.columns:
        return df

    out = df.copy()
    standalone = pd.Series(False, index=out.index)
    if "노출형태" in out.columns:
        exposure = out["노출형태"].fillna("").astype(str).str.strip()
        standalone |= exposure.str.contains("단독", na=False)
    if "단독매물" in out.columns:
        solo = out["단독매물"].fillna("").astype(str).str.strip().str.lower()
        standalone |= solo.isin({"1", "true", "y", "yes", "단독", "단독매물"})
    if "묶음내순위" in out.columns:
        standalone |= out["묶음내순위"].fillna("").astype(str).str.contains("단독", na=False)

    if "부동산명" in out.columns:
        realtor_key = out["부동산명"].map(clean_realtor_name)
        seller_counts = realtor_key.groupby(out["매물묶음키"], dropna=False).transform("nunique")
        standalone |= seller_counts <= 1

    before = len(out)
    out = out.loc[~standalone].copy()
    dropped = before - len(out)
    if dropped:
        print(f"[INFO] 단독 매물 제외: {dropped:,}행")
    return out


def _prepare_listing_identity(df: pd.DataFrame) -> pd.DataFrame:
    """분석 전 공통 식별키 보정: 방향 포함 + 단독 매물 제거."""
    return _drop_standalone_listings(_ensure_direction_and_tracking_key(df))


def _parse_ai_rec_ts(day_start: pd.Timestamp, advice: str) -> pd.Timestamp | None:
    """광고 추천 문구에서 당일 KST 시각 추출 (없으면 None)."""
    s = str(advice or "")
    if not s or "자유" in s:
        return None
    m = re.search(r"(\d{1,2})\s*:\s*(\d{2})", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d{1,2})시(?:\s*(\d{1,2})\s*분)?", s)
        if not m:
            return None
        h = int(m.group(1))
        g2 = m.group(2)
        mi = int(g2) if g2 else 0
    if h > 23 or mi > 59:
        return None
    return pd.Timestamp(
        year=int(day_start.year),
        month=int(day_start.month),
        day=int(day_start.day),
        hour=h,
        minute=mi,
        second=0,
        microsecond=0,
    )


def _ai_rec_ts_in_48h_window(
    advice: str,
    chart_day: datetime.date,
    day_start: pd.Timestamp,
    day_end: pd.Timestamp,
) -> pd.Timestamp | None:
    """48시간 창 안에 들어오는 추천 시각(종료일·전일 기준 HH:MM 각각 시도)."""
    for base_date in (chart_day, chart_day - timedelta(days=1)):
        base = pd.Timestamp(datetime.combine(base_date, datetime.min.time()))
        cand = _parse_ai_rec_ts(base, advice)
        if cand is not None and day_start <= cand <= day_end:
            return cand
    return None


def _parse_ai_secondary_time(advice: str) -> tuple[int, int] | None:
    """다중 추천 메시지에서 '2순위 HH:MM'을 추출. 없으면 None."""
    s = str(advice or "")
    if not s:
        return None
    m = re.search(r"2순위[^\d]*(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return h, mi
    return None


def _ai_secondary_ts_in_48h_window(
    advice: str,
    chart_day: datetime.date,
    day_start: pd.Timestamp,
    day_end: pd.Timestamp,
) -> pd.Timestamp | None:
    """48시간 창 안에 들어오는 2순위 추천 시각."""
    hm = _parse_ai_secondary_time(advice)
    if hm is None:
        return None
    h, mi = hm
    for base_date in (chart_day, chart_day - timedelta(days=1)):
        cand = pd.Timestamp(
            year=base_date.year, month=base_date.month, day=base_date.day,
            hour=h, minute=mi,
        )
        if day_start <= cand <= day_end:
            return cand
    return None


# ------------------------------------------------------------------------------
# 통합 액션 카드 (Integrated Action Card) — Mix Engine 헬퍼
# ------------------------------------------------------------------------------
def _parse_ai_primary_time(ai_msg: str) -> tuple[int, int] | None:
    """`💡 1순위: HH:MM ...` 형식 또는 (구) `💡 AI 처방: HH:MM ...`에서
    1순위 시각(시, 분)을 추출. 추출 실패 시 None."""
    if not ai_msg:
        return None
    s = str(ai_msg)
    m = re.search(r"1순위[^\d]*(\d{1,2}):(\d{2})", s)
    if not m:
        m = re.search(r"(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return h, mi
    return None


def _format_ai_recommendation_summary(ai_msg: str) -> str:
    """요약 행 col3용 — `AI 💡 1순위: HH:MM / 2순위: HH:MM` 형식."""
    s = (ai_msg or "").strip()
    if not s:
        return "AI · 추천 시각 확인 중"
    m1 = re.search(r"1순위[^\d]*(\d{1,2}):(\d{2})", s)
    m2 = re.search(r"2순위[^\d]*(\d{1,2}):(\d{2})", s)
    if m1 and m2:
        t1 = f"{int(m1.group(1)):02d}:{m1.group(2)}"
        t2 = f"{int(m2.group(1)):02d}:{m2.group(2)}"
        return f"AI 💡 1순위: {t1} / 2순위: {t2}"
    if m1:
        return f"AI 💡 1순위: {int(m1.group(1)):02d}:{m1.group(2)}"
    compact = re.sub(r"\s*\([^)]*\)", "", s).replace("💡", "").strip()
    return f"AI {compact}" if compact else "AI · 추천 시각 확인 중"


# Mix Engine 임계치
_AI_REACH_GRACE_MINUTES = 5      # AI 추천 시각으로부터 ±이 분량 이내면 "도달"로 간주
_WAIT_NEAR_THRESHOLD_MIN = 30    # 임박(30분 이내)이면 대기 메시지를 보강


def _determine_action_state(
    target_status: dict,
    any_waiting: bool,
    ai_msg: str,
    kst_now: pd.Timestamp,
) -> dict:
    """
    실시간 경쟁사 상황과 AI 다중 추천을 종합한 상태 판단.
    반환: {status, title, reason, palette}
        status: "STRIKE" | "WAIT" | "FREE"
        palette: 카드 색상 (bg / border / accent / text)
    """
    palette_strike = {
        "bg": "linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%)",
        "border": "#2563eb",
        "accent": "#1d4ed8",
        "text": "#1e3a8a",
        "subtext": "#1e40af",
    }
    palette_wait = {
        "bg": "linear-gradient(135deg, #fff7ed 0%, #ffedd5 100%)",
        "border": "#f97316",
        "accent": "#c2410c",
        "text": "#9a3412",
        "subtext": "#b45309",
    }
    palette_free = {
        "bg": "linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%)",
        "border": "#10b981",
        "accent": "#047857",
        "text": "#065f46",
        "subtext": "#047857",
    }

    # 데이터·경쟁사 자체가 없는 경우 → 자유 갱신
    if not target_status:
        return {
            "status": "FREE",
            "title": "✅ 자유 갱신",
            "reason": (
                "현재 감시 대상 경쟁사가 없거나 활동 데이터가 부족합니다. "
                "원하시는 시각에 자유롭게 갱신하셔도 됩니다."
            ),
            "palette": palette_free,
        }

    ai_primary = _parse_ai_primary_time(ai_msg)
    now_min = kst_now.hour * 60 + kst_now.minute
    waiting_cnt = sum(1 for info in target_status.values() if info.get("is_waiting"))

    # AI 추천 시각이 명시되지 않은 경우 (자유 갱신 메시지·파싱 실패)
    if ai_primary is None:
        if any_waiting:
            return {
                "status": "WAIT",
                "title": "🛑 대기 권장",
                "reason": (
                    f"요주의 경쟁사 {waiting_cnt}곳이 아직 활동 전입니다. "
                    "이들이 갱신을 마친 뒤 타격하면 노출 효과가 훨씬 오래갑니다."
                ),
                "palette": palette_wait,
            }
        return {
            "status": "STRIKE",
            "title": "🚀 AI 광고 추천 시간",
            "reason": (
                "주요 경쟁사들이 오늘 활동을 마쳤거나 일정 외 시간입니다. "
                "지금 갱신해도 빈집을 노릴 수 있습니다."
            ),
            "palette": palette_strike,
        }

    ai_min = ai_primary[0] * 60 + ai_primary[1]
    diff_min = ai_min - now_min
    hh, mm = ai_primary
    ai_hhmm = f"{hh:02d}:{mm:02d}"

    # (A) AI 추천 시각에 도달했거나 이미 지났음 → 지금 광고
    if diff_min <= _AI_REACH_GRACE_MINUTES:
        if not any_waiting:
            reason = (
                f"AI 1순위 추천 시각({ai_hhmm})에 도달했고, "
                "주요 경쟁사들이 활동을 마쳤습니다. 지금이 최적의 타이밍입니다."
            )
        else:
            reason = (
                f"AI 1순위 추천 시각({ai_hhmm})에 도달했습니다. "
                f"요주의 경쟁사 {waiting_cnt}곳이 남아있지만, 추천 시각의 빈집 점수가 더 높습니다."
            )
        return {
            "status": "STRIKE",
            "title": "🚀 AI 광고 추천 시간",
            "reason": reason,
            "palette": palette_strike,
        }

    # (B) 경쟁사들이 이미 모두 활동 종료 → 빈집이 일찍 열림
    if not any_waiting:
        return {
            "status": "STRIKE",
            "title": "🚀 AI 광고 추천 시간",
            "reason": (
                f"AI 1순위 추천 시각({ai_hhmm})까지 {diff_min//60}시간 {diff_min%60}분 남았지만, "
                "주요 경쟁사들이 이미 오늘 활동을 마쳤습니다. AI 시간을 기다리지 말고 지금 타격하세요."
            ),
            "palette": palette_strike,
        }

    # (C) AI 시간 도달 전 + 요주의 경쟁사 남음 → 대기 권장
    if diff_min <= _WAIT_NEAR_THRESHOLD_MIN:
        reason = (
            f"AI 1순위 추천 시각({ai_hhmm})까지 {diff_min}분 남았습니다. "
            f"요주의 경쟁사 {waiting_cnt}곳이 활동 중이니 이 시각을 지키는 것이 안전합니다."
        )
    else:
        reason = (
            f"AI 1순위 추천 시각({ai_hhmm})까지 {diff_min//60}시간 {diff_min%60}분 남았고, "
            f"요주의 경쟁사 {waiting_cnt}곳이 아직 활동 전입니다. "
            "지금 갱신하면 곧 경쟁사 갱신에 묻혀 효과가 빠르게 소멸됩니다."
        )

    return {
        "status": "WAIT",
        "title": "🛑 대기 권장",
        "reason": reason,
        "palette": palette_wait,
    }


def _render_action_card(
    action: dict,
    ai_msg: str,
    *,
    total_watch: int = 0,
    waiting_watch: int = 0,
) -> None:
    """통합 액션 카드 — 실시간 경쟁사 상태를 최우선으로 렌더."""
    if total_watch <= 0:
        title = "✅ 자유 갱신"
        palette = {"bg": "#FFFFFF", "border": "#25B196", "text": "#1A7F6C"}
        summary_text = "현재 화면에서 집계된 경쟁 감시 대상이 없습니다. 원하시는 시각에 자유롭게 갱신하셔도 됩니다."
    elif waiting_watch > 0:
        title = "🛑 대기 권장 (적군 활동 중)"
        palette = {"bg": "#FFFFFF", "border": "#E11D48", "text": "#9F1239"}
        summary_text = f"현재 감시 중인 총 {total_watch}곳 중 {waiting_watch}곳이 아직 갱신 대기 중입니다."
    else:
        title = "🚀 지금 광고 (경쟁사 활동 종료)"
        palette = {"bg": "#FFFFFF", "border": "#185294", "text": "#113A6A"}
        summary_text = f"감시 중인 {total_watch}곳이 모두 오늘 갱신을 마쳤습니다. 지금이 가장 안전한 타점입니다."

    raw_ai = (ai_msg or "").strip()
    ai_html = html.escape(raw_ai) if raw_ai else "AI 추천 문구를 확인 중입니다."
    ai_block = (
        "<div style='margin-top:12px; font-size:0.85rem; color:#64748B; line-height:1.5;'>"
        "<span style='font-weight:700; color:#475569;'>[AI 타점 분석]</span> "
        f"<span style='font-weight:500;'>{ai_html}</span>"
        "</div>"
    )

    st.markdown(
        f"""
        <div style="background: {palette['bg']}; border: 1px solid #E2E8F0; border-left: 6px solid {palette['border']}; border-radius: 8px; padding: 20px 24px; margin: 16px 0; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.02); font-family: inherit;">
          <div style="font-size: 1.35rem; font-weight: 800; color: {palette['border']}; letter-spacing: -0.02em; line-height: 1.25;">{title}</div>
          <div style="font-size: 1.05rem; color: {palette['text']}; margin-top: 8px; line-height: 1.5; font-weight: 600;">{html.escape(summary_text)}</div>
          {ai_block}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _renewal_events_28d_for_task(task: str, td: pd.DataFrame, today_d: datetime.date) -> int:
    if td.empty or "Task" not in td.columns:
        return 0
    sub = td[td["Task"] == task].copy()
    if sub.empty:
        return 0
    sub["_ts"] = pd.to_datetime(sub["수집일시"], errors="coerce")
    sub = sub[sub["_ts"].notna()]
    start_d = today_d - timedelta(days=27)
    sub = sub[sub["_ts"].dt.date >= start_d]
    if sub.empty:
        return 0
    dedup_cols = [c for c in ("매물묶음키", "수집일시", "확인일자") if c in sub.columns]
    if dedup_cols:
        return len(sub.drop_duplicates(subset=dedup_cols))
    return len(sub.drop_duplicates(subset=["수집일시"]))


def _action_urgency_rank(action: dict) -> int:
    """긴급도 정렬: 🚀(STRIKE) → 🛑(WAIT) → ✅(FREE)."""
    return {"STRIKE": 0, "WAIT": 1, "FREE": 2}.get(str(action.get("status", "")), 9)


_TASK_SORT_OPTIONS = ("광고 횟수 순", "이름 오름차순")


def _dong_ho_sort_key(task: str) -> str:
    """Task 라벨에서 동/호수(앞부분) 추출 — 이름 정렬용."""
    s = str(task).strip()
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    return s


def _task_ad_freq_in_action_df(task: str, action_df: pd.DataFrame) -> int:
    """action_df에서 해당 Task의 실제 '광고 갱신 횟수' 값을 찾아 반환."""
    if action_df is None or action_df.empty or "Task" not in action_df.columns:
        return 0

    t_clean = str(task).replace(" ", "")
    mask = action_df["Task"].astype(str).str.replace(" ", "") == t_clean

    if mask.any():
        if "광고 갱신 횟수" in action_df.columns:
            val = action_df.loc[mask, "광고 갱신 횟수"].iloc[0]
            try:
                return int(pd.to_numeric(val, errors="coerce"))
            except (ValueError, TypeError):
                return 0
    return 0


def _sort_tracking_tasks(
    tasks: list,
    action_df: pd.DataFrame,
    sort_mode: str,
) -> list[str]:
    """tracking Task 목록 정렬 — 기본은 광고 횟수 순, 보조 옵션은 이름 오름차순."""
    items = [str(t) for t in tasks if str(t).strip()]
    if sort_mode == "이름 오름차순":
        return sorted(items, key=_dong_ho_sort_key, reverse=False)
    return sorted(
        items,
        key=lambda t: (-_task_ad_freq_in_action_df(t, action_df), _dong_ho_sort_key(t)),
    )


def _render_tracking_tab_header(title_html: str, *, widget_key: str) -> str:
    """탭 제목(좌) + 정렬 셀렉트(우). 세션 `tl_task_sort`로 탭 간 동기화."""
    if "tl_task_sort" not in st.session_state:
        st.session_state.tl_task_sort = _TASK_SORT_OPTIONS[0]
    hdr_l, hdr_r = st.columns([3, 1])
    with hdr_l:
        st.markdown(title_html, unsafe_allow_html=True)
    with hdr_r:
        cur = st.session_state.tl_task_sort
        idx = list(_TASK_SORT_OPTIONS).index(cur) if cur in _TASK_SORT_OPTIONS else 0
        picked = st.selectbox(
            "정렬 기준",
            list(_TASK_SORT_OPTIONS),
            index=idx,
            key=widget_key,
        )
        st.session_state.tl_task_sort = picked
    return str(st.session_state.tl_task_sort)


def _status_badge_from_action(action: dict) -> tuple[str, str]:
    """(표시 라벨, 포인트 컬러)"""
    st_code = str(action.get("status", "FREE"))
    if st_code == "STRIKE":
        return "🚀 지금 광고", "#185294"
    if st_code == "WAIT":
        return "🛑 대기 권장", "#E11D48"
    return "✅ 자유 갱신", "#25B196"


def _command_summary_cell(text: str, *, accent: str | None = None) -> None:
    """요약 행 셀 — 이미지와 동일한 카드형 박스, 일반 굵기(400)."""
    border_left = f"border-left:4px solid {accent};" if accent else ""
    st.markdown(
        f"<div style='background:#FFFFFF;border:1px solid #E2E8F0;{border_left}"
        f"border-radius:8px;padding:12px 14px;min-height:52px;"
        f"display:flex;align-items:center;font-size:1rem;font-weight:400;"
        f"color:#0F172A;line-height:1.45;word-break:break-word;'>"
        f"{html.escape(text)}</div>",
        unsafe_allow_html=True,
    )


def _command_summary_cell_ai(summary: str) -> None:
    """요약 행 col3 — AI 접두어만 포인트 컬러, 본문은 일반체."""
    body = summary[3:].strip() if summary.startswith("AI") else summary
    st.markdown(
        "<div style='background:#FFFFFF;border:1px solid #E2E8F0;border-radius:8px;"
        "padding:12px 14px;min-height:52px;display:flex;align-items:center;"
        "font-size:1rem;font-weight:400;color:#0F172A;line-height:1.45;word-break:break-word;'>"
        "<span style='color:#1E3A8A;font-weight:400'>AI</span> "
        f"{html.escape(body)}</div>",
        unsafe_allow_html=True,
    )


def _render_command_summary_row(
    task: str,
    target_status: dict,
    *,
    status_label: str,
    status_accent: str,
    ai_summary: str,
    expander_key: str,
) -> None:
    """요약 행(상태|매물|AI) 3컬럼 + 하단 상세보기 익스팬더."""
    col1, col2, col3 = st.columns([1, 1.5, 2.0])
    with col1:
        _command_summary_cell(status_label, accent=status_accent)
    with col2:
        _command_summary_cell(str(task))
    with col3:
        _command_summary_cell_ai(ai_summary)

    st.markdown("<div style='height:5px;margin:0;padding:0;'></div>", unsafe_allow_html=True)
    with st.expander("상세보기", expanded=False, key=expander_key):
        _render_competitor_watch_section(task, target_status)

    st.write("")


def _find_action_row_for_task(task: str, action_df: pd.DataFrame) -> pd.Series | None:
    """타임라인 Task 문자열에 대응하는 action_df 행 엄격 탐색"""
    if action_df.empty:
        return None
    t_nospace = str(task).replace(" ", "")

    if "Task" in action_df.columns:
        for _, row in action_df.iterrows():
            tk = str(row.get("Task", "")).replace(" ", "")
            if tk == t_nospace:
                return row

    if "매물명" in action_df.columns:
        for _, row in action_df.iterrows():
            mn = str(row.get("매물명", "")).replace(" ", "")
            if t_nospace in mn:
                return row

    return None



def _build_target_status_for_task(
    sel_task: str,
    t_df: pd.DataFrame,
    complex_data: dict,
    comp_df: pd.DataFrame,
    *,
    kst_now: pd.Timestamp,
    kst_today: datetime.date,
    my_unified: str,
    is_demo_mode: bool,
    is_unauth_demo: bool,
    filter_realtor_name: str,
    display_realtor: str,
) -> dict:
    """선택 매물 기준 경쟁사 감시 상태 dict (Live Tracker와 동일 로직)."""
    sub_df = t_df[t_df["Task"] == sel_task].copy()
    target_status: dict = {}
    if sub_df.empty:
        return target_status

    b_df = complex_data.get("boosted")
    if b_df is not None and not b_df.empty:
        b_df = b_df.copy()
        b_df["수집일시"] = pd.to_datetime(b_df["수집일시"], errors="coerce")
        if "부동산명_정제" not in b_df.columns:
            b_df["부동산명_정제"] = b_df["부동산명"].apply(clean_realtor_name)
        today_renewed = b_df[b_df["수집일시"].dt.date == kst_today]["부동산명_정제"].unique().tolist()
        b_freq = b_df.dropna(subset=["수집일시"])
        analysis_days = max(
            1,
            (b_freq["수집일시"].max().date() - b_freq["수집일시"].min().date()).days + 1,
        )
        renew_counts = b_freq.groupby("부동산명_정제", dropna=False).size()
        high_freq_unified = [r for r, cnt in renew_counts.items() if (analysis_days / cnt) <= 4.0]
    else:
        today_renewed = []
        high_freq_unified = []

    latest_ranks = sub_df.groupby("부동산명_통합")["묶음내순위_숫자"].last().reset_index()
    latest_ranks["묶음내순위_숫자"] = (
        pd.to_numeric(latest_ranks["묶음내순위_숫자"], errors="coerce").fillna(999)
    )
    top3_unified = latest_ranks[latest_ranks["묶음내순위_숫자"] <= 3]["부동산명_통합"].tolist()
    sub_realtors = sub_df["부동산명_통합"].unique().tolist()
    high_freq_unified = [r for r in high_freq_unified if r in sub_realtors]

    for r_uni in set(top3_unified + high_freq_unified):
        if r_uni == my_unified:
            continue
        r_original_series = sub_df[sub_df["부동산명_통합"] == r_uni]["부동산명"]
        if r_original_series.empty:
            continue
        r_original = r_original_series.iloc[-1]
        r_disp = mask_text(
            r_original,
            is_demo=is_demo_mode,
            filter_realtor_name=filter_realtor_name,
            display_realtor=display_realtor,
        )
        r_disp_short = html.escape(_strip_realtor_label_noise(r_disp))
        is_today = r_uni in today_renewed
        last_active_hhmm = _last_renewal_hhmm_today(
            r_uni, kst_today, b_df if b_df is not None else None, sub_df
        )
        peak_usual = "패턴 불규칙"
        peak_today_wd = "-"
        wd_group = ""
        deadline = 18
        freq_str = "갱신 패턴 산출 전"
        weekday_real = True
        if not comp_df.empty and "부동산명" in comp_df.columns:
            comp_match = comp_df.copy()
            comp_match["부동산명_통합"] = comp_match["부동산명"].apply(clean_realtor_name)
            cm = comp_match.loc[comp_match["부동산명_통합"] == r_uni]
            if not cm.empty:
                row0 = cm.iloc[0]
                if "갱신빈도" in cm.columns:
                    fv = row0.get("갱신빈도")
                    if pd.notna(fv) and str(fv).strip():
                        freq_str = str(fv)
                if "주력 갱신 시간" in cm.columns and pd.notna(row0.get("주력 갱신 시간")):
                    peak_usual = str(row0["주력 갱신 시간"])
                if "오늘 요일 주력 시간" in cm.columns and pd.notna(row0.get("오늘 요일 주력 시간")):
                    peak_today_wd = str(row0["오늘 요일 주력 시간"])
                if "오늘_요일_그룹" in cm.columns and pd.notna(row0.get("오늘_요일_그룹")):
                    wd_group = str(row0["오늘_요일_그룹"])
                if "오늘 요일 마지노선" in cm.columns:
                    try:
                        deadline = int(float(row0["오늘 요일 마지노선"]))
                    except (TypeError, ValueError):
                        deadline = 18
                if "오늘요일_실측" in cm.columns:
                    _wr = row0.get("오늘요일_실측")
                    weekday_real = True if pd.isna(_wr) else bool(_wr)
        now_hour = kst_now.hour
        _bad_peak = ("-", "패턴 불규칙", "", "nan")
        peak_usual_n = str(peak_usual).strip()
        peak_today_n = str(peak_today_wd).strip()
        if peak_today_n.lower() in ("nan", "none"):
            peak_today_n = "-"
        wd_disp = wd_group if wd_group else ""
        if not weekday_real and wd_disp:
            if peak_usual_n not in _bad_peak:
                pattern_desc = f"{wd_disp} 패턴: 데이터 없음 (전체 기준 {peak_usual_n})"
            else:
                pattern_desc = f"{wd_disp} 패턴: 데이터 없음 (평일 기준 분석)"
        elif peak_usual_n in _bad_peak or peak_today_n in _bad_peak:
            pattern_desc = "패턴 데이터 부족"
        elif peak_usual_n == peak_today_n:
            pattern_desc = f"평소처럼 {peak_today_n}에 집중합니다"
        else:
            wg = f"{wd_disp} " if wd_disp else ""
            pattern_desc = f"평소 {peak_usual_n} ➔ {wg}{peak_today_n} 위주"
        _gray = "color:#64748b;font-size:0.9rem;"
        _small = "color:#94a3b8;font-size:0.8rem;"
        if is_today:
            state_html = (
                f"<b>🟢 오늘 광고 완료 ({last_active_hhmm} 진행)</b>"
                f"<br><span style='{_gray}'>{html.escape(pattern_desc)}</span>"
                f"<br><span style='{_small}'>마지노선: {deadline}시</span>"
            )
            is_waiting = False
        elif now_hour < deadline:
            state_html = (
                f"<b>🔴 아직 활동 전 (주의)</b>"
                f"<br><span style='{_gray}'>{html.escape(pattern_desc)}</span>"
                f"<br><span style='{_small}'>마지노선: {deadline}시 (이후 안전)</span>"
            )
            is_waiting = True
        else:
            state_html = (
                f"<b><span style='color:#3B82F6;'>🔵 활동 없음 (마지노선 경과)</span></b>"
                f"<br><span style='{_gray}'>{html.escape(pattern_desc)}</span>"
                f"<br><span style='{_small}'>마지노선 {deadline}시 경과</span>"
            )
            is_waiting = False
        if r_uni in top3_unified:
            target_status[r_uni] = {
                "display": r_disp,
                "display_short": r_disp_short,
                "freq": freq_str,
                "icon": "👑",
                "html": state_html,
                "is_waiting": is_waiting,
                "is_done_today": is_today,
                "last_active_time": last_active_hhmm,
                "type": "상위권 방어조",
            }
        else:
            target_status[r_uni] = {
                "display": r_disp,
                "display_short": r_disp_short,
                "freq": freq_str,
                "icon": "🔥",
                "html": state_html,
                "is_waiting": is_waiting,
                "is_done_today": is_today,
                "last_active_time": last_active_hhmm,
                "type": "고빈도 추격조",
            }
    if is_unauth_demo and target_status:
        _apply_unauth_demo_watch_target_overrides(target_status)
    return target_status


def _render_competitor_watch_section(sel_task: str, target_status: dict) -> None:
    """👁️ 감시 중인 경쟁사 상세 카드 섹션."""
    if not target_status:
        return
    st.markdown(
        f"👁️ 감시 중인 경쟁사 상세 "
        f"({html.escape(str(sel_task))} 기준 · {len(target_status)}곳)"
    )
    st.caption(
        "※ 광고 여부는 단지 전체 기준으로 감시하며, "
        "현재 선택한 매물을 보유한 부동산만 표시됩니다."
    )

    def _freq_sort_score(info: dict) -> int:
        freq = str(info.get("freq", ""))
        if "매일" in freq:
            return 5
        if "2일" in freq:
            return 4
        if "3~4일" in freq:
            return 3
        if "주 1~2회" in freq:
            return 2
        if "비정기" in freq:
            return 1
        return 0

    sorted_targets = sorted(
        target_status.items(),
        key=lambda x: (
            -_freq_sort_score(x[1]),
            not x[1].get("is_waiting"),
            str(x[1].get("display", "")),
        ),
    )

    def _render_competitor_cards(items: list[tuple[str, dict]]) -> None:
        if not items:
            return
        cols = st.columns(4)
        for col_idx, (_, info) in enumerate(items):
            _card_bg = "#FFFFFF"
            _title = info.get("display_short") or html.escape(str(info.get("display", "")))
            _freq_e = html.escape(str(info.get("freq", "")))
            cols[col_idx % 4].markdown(
                f"<div style='height:180px; overflow-y:hidden; display:flex; "
                f"flex-direction:column; justify-content:space-between; padding:15px; "
                f"border-radius:8px; border:1px solid #E2E8F0; background-color:{_card_bg}; "
                f"box-shadow: 0 1px 2px rgba(0,0,0,0.02); margin-bottom:10px; box-sizing:border-box;'>"
                f"<div>"
                f"<div style='font-size:0.72rem; color:#64748b; margin-bottom:4px;'>"
                f"{html.escape(str(info['icon']))} {html.escape(str(info['type']))}</div>"
                f"<div style='font-weight:800; font-size:0.95rem; color:#1e293b; "
                f"line-height:1.25; margin-bottom:4px;'>{_title} "
                f"<span style='font-size:0.76rem; font-weight:500; color:#475569;'>"
                f"({_freq_e})</span></div>"
                f"</div>"
                f"<div style='font-size:0.86rem; line-height:1.35;'>{info['html']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    visible_targets = sorted_targets[:8]
    hidden_targets = sorted_targets[8:]
    _render_competitor_cards(visible_targets)
    if hidden_targets:
        with st.expander(f"더보기 ({len(hidden_targets)}곳)", expanded=False):
            _render_competitor_cards(hidden_targets)


def _friendly_hover_state_label(state: str) -> str:
    """툴팁용 사람이 읽기 쉬운 상태 문구."""
    s = str(state or "")
    if "방어" in s:
        return "✅ 상위권 안정 방어 중"
    return "🚨 경쟁사 진입! 갱신 추천"


def _parse_final_effect_display_ts(chart_day: datetime.date, s: str) -> pd.Timestamp | None:
    """action_df `최종 효력 유지 시각` (%m/%d %H:%M) 파싱."""
    raw = str(s).strip()
    if not raw or raw == "—":
        return None
    m = re.match(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", raw)
    if not m:
        return None
    mo, d, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    try:
        return pd.Timestamp(
            year=chart_day.year,
            month=mo,
            day=d,
            hour=h,
            minute=mi,
            second=0,
            microsecond=0,
        )
    except ValueError:
        return None


def _reference_guide_timestamp(
    action_df: pd.DataFrame,
    chart_day: datetime.date,
    day_start: pd.Timestamp,
    day_end: pd.Timestamp,
) -> pd.Timestamp:
    """가이드 세로선: action_df 최종 효력 시각 최대 vs 현재 시각 중 차트 구간 안에서 의미 있게."""
    now = _now_kst_naive()
    candidates = [min(max(now, day_start), day_end)]
    if not action_df.empty and "최종 효력 유지 시각" in action_df.columns:
        for s in action_df["최종 효력 유지 시각"].tolist():
            ts = _parse_final_effect_display_ts(chart_day, s)
            if ts is None:
                ts = _parse_final_effect_display_ts(chart_day - timedelta(days=1), s)
            if ts is not None and day_start <= ts <= day_end:
                candidates.append(ts)
    ref = max(candidates)
    return min(max(ref, day_start), day_end)


def _clip_timeline_to_chart_day(
    timeline_df: pd.DataFrame, chart_day: datetime.date
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """chart_day 기준 어제 00:00 ~ chart_day 23:59 (48시간) 구간으로 클립."""
    day_start = pd.Timestamp(datetime.combine(chart_day - timedelta(days=1), datetime.min.time()))
    day_end = (
        pd.Timestamp(datetime.combine(chart_day, datetime.min.time()))
        + pd.Timedelta(days=1)
        - pd.Timedelta(seconds=1)
    )
    if timeline_df.empty:
        return timeline_df.copy(), day_start, day_end
    out = timeline_df.copy()
    out["Start"] = pd.to_datetime(out["Start"], errors="coerce")
    out["Finish"] = pd.to_datetime(out["Finish"], errors="coerce")
    s_clip = out["Start"].clip(lower=day_start, upper=day_end)
    f_clip = out["Finish"].clip(lower=day_start, upper=day_end)
    out["Start"] = s_clip
    out["Finish"] = f_clip
    out = out[out["Finish"] > out["Start"]].copy()
    return out, day_start, day_end


def _empty_timeline_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["Task", "Start", "Finish", "State", "내순위", "Top1부동산"])


def _mask_agent_name_for_tooltip(
    name: str, *, is_demo: bool, filter_realtor_name: str, display_realtor: str
) -> str:
    """app.py `mask_text(..., is_agent=True)`와 동일 정책(데모 시 경쟁사 마스킹)."""
    if not is_demo:
        return str(name or "—").strip() or "—"
    s = str(name or "").strip()
    if not s or s == "—":
        return "—"
    if filter_realtor_name in s:
        return display_realtor
    stable_id = sum(ord(c) * (i + 1) for i, c in enumerate(s)) % 1000
    return f"경쟁사 {stable_id:03d}"


def mask_text(
    text,
    *,
    is_demo: bool,
    filter_realtor_name: str,
    display_realtor: str,
    is_agent: bool = True,
) -> str:
    """app.py `mask_text`와 동일: 데모 시 중개사명 마스킹."""
    if not is_demo:
        return str(text)
    if is_agent:
        s = str(text)
        if filter_realtor_name in s:
            return display_realtor
        stable_id = sum(ord(c) * (i + 1) for i, c in enumerate(s)) % 1000
        return f"경쟁사 {stable_id:03d}"
    return re.sub(r"\d", "*", str(text))


@st.cache_data(show_spinner=False)
def _build_plotly_hover_frame(
    tl_plot: pd.DataFrame,
    is_demo_mode: bool,
    filter_realtor_name: str,
    display_realtor: str,
) -> pd.DataFrame:
    """Plotly timeline용 호버/커스텀데이터 컬럼 생성 (동일 `tl_plot` 재선택 시 캐시)."""
    tl_hover = tl_plot.copy()
    if "내순위" not in tl_hover.columns:
        tl_hover["내순위"] = 0
    if "Top1부동산" not in tl_hover.columns:
        tl_hover["Top1부동산"] = "—"
    _red_tl_state = "🔴 경쟁사 진입 (순위 밀림)"
    tl_hover["_hv_s"] = tl_hover["Start"].dt.strftime("%H:%M")
    tl_hover["_hv_f"] = tl_hover["Finish"].dt.strftime("%H:%M")
    tl_hover["_hv_st"] = tl_hover["State"].map(
        lambda s: "🔴 경쟁사 진입" if s == _red_tl_state else "🟢 방어 중"
    )

    def _fmt_rank_cell(v) -> str:
        n = int(pd.to_numeric(v, errors="coerce") or 0)
        return f"{n}위" if n > 0 else "—"

    tl_hover["_hv_rank"] = tl_hover["내순위"].map(_fmt_rank_cell)
    tl_hover["_hv_top1_m"] = tl_hover["Top1부동산"].apply(
        lambda x: _mask_agent_name_for_tooltip(
            str(x) if pd.notna(x) else "—",
            is_demo=is_demo_mode,
            filter_realtor_name=filter_realtor_name,
            display_realtor=display_realtor,
        )
    )
    tl_hover["_hv_extra"] = tl_hover.apply(
        lambda r: (
            f"1위 부동산: {r['_hv_top1_m']}<br>"
            if r["State"] == _red_tl_state
            else ""
        ),
        axis=1,
    )
    return tl_hover


def _now_kst_naive() -> pd.Timestamp:
    KST = timezone(timedelta(hours=9))
    return pd.Timestamp(datetime.now(KST).replace(tzinfo=None))


def _seconds_effective_excluding_night(t0: pd.Timestamp, t1: pd.Timestamp) -> float:
    """[t0, t1] 구간 초에서 매일 00:00:00~07:59:59 겹침을 제외."""
    t0 = pd.Timestamp(t0)
    t1 = pd.Timestamp(t1)
    if pd.isna(t0) or pd.isna(t1) or t1 <= t0:
        return 0.0
    total = (t1 - t0).total_seconds()
    night = 0.0
    d = t0.normalize()
    end_d = t1.normalize()
    while d <= end_d:
        lo = d
        hi = d + pd.Timedelta(hours=7, minutes=59, seconds=59)
        o0 = max(t0, lo)
        o1 = min(t1, hi)
        if o1 > o0:
            night += (o1 - o0).total_seconds()
        d += pd.Timedelta(days=1)
    return max(0.0, total - night)


def _timeline_efficiency_score_from_tl_plot(tl_plot: pd.DataFrame) -> float:
    """tl_plot 행별 영업 유효시간(심야 제외) 합으로 초록/전체 비율 → 100점 만점."""
    if tl_plot.empty:
        return 0.0
    green_state = "🟢 1~3위 방어 중"
    total_s = 0.0
    green_s = 0.0
    for _, row in tl_plot.iterrows():
        eff = _seconds_effective_excluding_night(row["Start"], row["Finish"])
        total_s += eff
        if str(row.get("State", "")) == green_state:
            green_s += eff
    if total_s <= 0:
        return 0.0
    return float((green_s / total_s) * 100.0)


@st.cache_data(show_spinner=False)
def _merge_last_trigger_ts(
    hist: pd.DataFrame, b_work: pd.DataFrame, key_cols: tuple[str, ...]
) -> pd.DataFrame:
    """각 수집일시 행에 대해 직전 광고 갱신(트리거) 시각을 붙입니다."""
    _k_list = list(key_cols)
    trig = b_work.dropna(subset=_k_list + ["수집일시"]).copy()
    trig["last_trigger_ts"] = pd.to_datetime(trig["수집일시"], errors="coerce")
    trig = trig.dropna(subset=_k_list + ["last_trigger_ts"])
    trig = trig[_k_list + ["last_trigger_ts"]].drop_duplicates().sort_values(_k_list + ["last_trigger_ts"])
    out = hist.reset_index(drop=True).copy()
    out["_row_ord"] = out.index
    parts: list[pd.DataFrame] = []
    grouped = out.groupby(_k_list, dropna=False, sort=False)
    for key, g in grouped:
        left = g.sort_values("수집일시")
        r = trig
        if isinstance(key, tuple):
            for i, c in enumerate(_k_list):
                r = r[r[c] == key[i]]
        else:
            r = r[r[_k_list[0]] == key]
        r = r.sort_values("last_trigger_ts")
        if r.empty:
            merged = left.assign(last_trigger_ts=pd.NaT)
        else:
            r_ts = r[["last_trigger_ts"]].sort_values("last_trigger_ts")
            merged = pd.merge_asof(
                left.sort_values("수집일시"),
                r_ts,
                left_on="수집일시",
                right_on="last_trigger_ts",
                direction="backward",
            )
        parts.append(merged)
    stacked = pd.concat(parts, ignore_index=True).sort_values("_row_ord")
    return stacked.drop(columns=["_row_ord"]).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def _tl_enrich_rank_top1_merge_asof(
    tl: pd.DataFrame,
    t_work: pd.DataFrame,
    my_name_unified: str,
) -> pd.DataFrame:
    """Start 시각 기준 merge_asof(backward)로 내 순위·1위 부동산 부착. (for 루프 제거 및 초고속 일괄 매칭)"""
    if tl.empty:
        return tl
    out = tl.copy()
    out["_tl_idx"] = range(len(out))

    # 빈 문자열이나 결측치를 안전하게 처리
    out["_bkey"] = out["매물묶음키"].astype(str).str.strip().replace("nan", "")
    out["Start"] = pd.to_datetime(out["Start"], errors="coerce")

    tw = t_work.copy()
    tw["수집일시"] = pd.to_datetime(tw["수집일시"], errors="coerce")
    tw["부동산명_통합"] = tw["부동산명"].apply(clean_realtor_name)
    tw["_rnk"] = pd.to_numeric(tw["묶음내순위_숫자"], errors="coerce")
    tw["_bkey"] = tw["매물묶음키"].astype(str).str.strip().replace("nan", "")

    my_track = tw[tw["부동산명_통합"] == my_name_unified][["_bkey", "수집일시", "_rnk"]].copy()
    my_track = my_track.rename(columns={"수집일시": "_ts_mine", "_rnk": "_rank_m"})
    my_track = my_track.dropna(subset=["_ts_mine", "_bkey"]).sort_values("_ts_mine")

    top1_track = tw[tw["_rnk"] == 1][["_bkey", "수집일시", "부동산명"]].copy()
    top1_track = top1_track.rename(columns={"수집일시": "_ts_top1", "부동산명": "_name_top1"})
    top1_track = top1_track.dropna(subset=["_ts_top1", "_bkey"]).sort_values("_ts_top1")

    # [핵심 최적화] for 루프를 제거하고, by="_bkey" 를 이용해 전체 데이터를 한 방에 merge_asof
    out = out.sort_values("Start").dropna(subset=["Start", "_bkey"])

    if not my_track.empty:
        out = pd.merge_asof(
            out,
            my_track,
            left_on="Start",
            right_on="_ts_mine",
            by="_bkey",
            direction="backward",
        )
    else:
        out["_rank_m"] = pd.NA

    if not top1_track.empty:
        out = pd.merge_asof(
            out,
            top1_track,
            left_on="Start",
            right_on="_ts_top1",
            by="_bkey",
            direction="backward",
        )
    else:
        out["_name_top1"] = pd.NA

    # 1위명 ffill을 단지 전체가 아닌 매물 묶음키(_bkey) 단위로 일괄 수행
    out["_name_top1"] = out.groupby("_bkey", dropna=False)["_name_top1"].ffill()

    out = out.sort_values("_tl_idx")
    r_asof = pd.to_numeric(out["_rank_m"], errors="coerce")
    r_hist = pd.to_numeric(out["묶음내순위_숫자"], errors="coerce")
    out["내순위"] = pd.to_numeric(r_asof.combine_first(r_hist), errors="coerce").fillna(0).astype(int)

    def _clean_top1_cell(x) -> str:
        s = str(x).strip()
        return "—" if not s or s.lower() in ("nan", "none", "nat") else s

    out["Top1부동산"] = out["_name_top1"].map(_clean_top1_cell)
    out = out.drop(columns=["_tl_idx", "_bkey", "_rank_m", "_name_top1", "_ts_mine", "_ts_top1"], errors="ignore")
    return out


@st.cache_data(show_spinner=False)
def _build_timeline_from_hist(
    hist_base: pd.DataFrame,
    my_name_unified: str,
    my_hist: pd.DataFrame,
    batch_end_ts: pd.Timestamp,
    t_work: pd.DataFrame,
) -> pd.DataFrame:
    """hist_base → Plotly timeline용 Task / Start / Finish / State + 순위·1위 부동산(원본명)."""
    tl_src = hist_base[hist_base["부동산명_통합"] == my_name_unified].copy()
    if tl_src.empty:
        return _empty_timeline_df()

    spec_src = my_hist.sort_values("수집일시").copy()
    spec_src["동/호수"] = spec_src["동/호수"].astype(str).str.strip()
    spec_src["층/타입"] = spec_src["층/타입"].astype(str).str.strip()
    if "방향" not in spec_src.columns:
        spec_src["방향"] = ""
    spec_src["방향"] = spec_src["방향"].fillna("").astype(str).str.strip()
    rows_spec = []
    for bkey, grp in spec_src.groupby("매물묶음키", dropna=False):

        def _last_non_empty(series):
            s = [x for x in series.tolist() if str(x).strip() and str(x).strip().lower() != "nan"]
            return s[-1] if s else ""

        g_price = _last_non_empty(grp["가격"]) if "가격" in grp.columns else ""
        rows_spec.append(
            {
                "매물묶음키": bkey,
                "단지명": _last_non_empty(grp["단지명"]),
                "동/호수": _last_non_empty(grp["동/호수"]),
                "층/타입": _dedup_floor_type_text(_last_non_empty(grp["층/타입"])),
                "가격": g_price,
                "방향": _last_non_empty(grp["방향"]) or _extract_direction_text(_last_non_empty(grp["층/타입"])),
            }
        )
    spec_df = pd.DataFrame(rows_spec)
    tl = tl_src.merge(spec_df, on="매물묶음키", how="left")

    now_kst = _now_kst_naive()
    tl["Start"] = pd.to_datetime(tl["수집일시"], errors="coerce")
    raw_next = pd.to_datetime(tl["다음수집일시"], errors="coerce")
    end_cap = batch_end_ts if pd.notna(batch_end_ts) else now_kst
    tl["Finish"] = raw_next.fillna(end_cap)
    tl["Finish"] = pd.to_datetime(tl["Finish"], errors="coerce")
    bad = tl["Finish"].isna() | (tl["Finish"] <= tl["Start"])
    tl.loc[bad, "Finish"] = tl.loc[bad, "Start"] + pd.Timedelta(seconds=1)

    if "가격" not in tl.columns:
        tl["가격"] = ""
    else:
        tl["가격"] = tl["가격"].fillna("")

    tl["Task"] = [
        _task_label_from_spec(d, dh, ft, _scalar_price_str(pr), direction)
        for d, dh, ft, pr, direction in zip(
            tl["단지명"].tolist(),
            tl["동/호수"].tolist(),
            tl["층/타입"].tolist(),
            tl["가격"].tolist(),
            tl.get("방향", pd.Series([""] * len(tl), index=tl.index)).tolist(),
        )
    ]

    def _state_row(is_top):
        if bool(is_top):
            return "🟢 1~3위 방어 중"
        return "🔴 경쟁사 진입 (순위 밀림)"

    tl["State"] = tl["is_top_tier"].fillna(False).map(_state_row)

    tl = _tl_enrich_rank_top1_merge_asof(tl, t_work, my_name_unified)

    out = tl[["Task", "Start", "Finish", "State", "내순위", "Top1부동산"]].dropna(subset=["Start"])
    return out


@st.cache_data(show_spinner=False)
def _build_prime_action_df(
    trk: pd.DataFrame,
    boosted_df: pd.DataFrame,
    filter_realtor_name: str,
    comp_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    app.py 마스터 대시보드와 동일한 merge_asof / death_ts 기반 action_df + hist_base 기반 timeline_df.
    """
    t_work = _ensure_direction_and_tracking_key(trk)
    b_work = _ensure_direction_and_tracking_key(boosted_df)
    t_work["수집일시"] = pd.to_datetime(t_work["수집일시"], errors="coerce")
    b_work["수집일시"] = pd.to_datetime(b_work["수집일시"], errors="coerce")
    ref_ts = b_work["수집일시"].max()
    if pd.notna(ref_ts):
        # 28일(4주) 분석 창: ref일 포함 28일분 → 달력 기준 27일 전 자정부터
        win_start = ref_ts.normalize() - pd.Timedelta(days=27)
        b_work = b_work[b_work["수집일시"] >= win_start].copy()
    t_work["부동산명_통합"] = t_work["부동산명"].apply(clean_realtor_name)
    b_work["부동산명_통합"] = b_work["부동산명"].apply(clean_realtor_name)
    my_name_unified = clean_realtor_name(filter_realtor_name)

    my_hist = t_work[t_work["부동산명_통합"] == my_name_unified].copy()

    if my_hist.empty:
        return _empty_action_df(), _empty_timeline_df()

    key_cols = ["매물묶음키", "부동산명_통합"]
    if "방향" not in key_cols:
        key_cols.append("방향")
    latest_ts = t_work["수집일시"].max()
    if pd.isna(latest_ts):
        return _empty_action_df(), _empty_timeline_df()

    batch_end_ts = latest_ts
    evt_cols = key_cols + ["수집일시"]

    hist_cols = list(
        dict.fromkeys(
            key_cols + ["수집일시", "묶음내순위_숫자", "전체순위_숫자", "노출형태", "방향"]
        )
    )
    hist_base = (
        t_work[hist_cols].dropna(subset=evt_cols).sort_values(key_cols + ["수집일시"]).copy()
    )
    hist_base["_rank_for_dedup"] = pd.to_numeric(hist_base["묶음내순위_숫자"], errors="coerce").fillna(999)
    hist_base = (
        hist_base.sort_values(key_cols + ["수집일시", "_rank_for_dedup"], kind="mergesort")
        .drop_duplicates(subset=key_cols + ["수집일시"], keep="first")
        .drop(columns=["_rank_for_dedup"])
        .copy()
    )
    valid_ranks = t_work[t_work["묶음내순위_숫자"] < 999]
    global_b_counts = valid_ranks.groupby("매물묶음키")["묶음내순위_숫자"].max()
    hist_base["묶음_총개수"] = hist_base["매물묶음키"].map(global_b_counts).fillna(1)

    hist_base["수집일시"] = pd.to_datetime(hist_base["수집일시"], errors="coerce")

    rank_num = pd.to_numeric(hist_base["묶음내순위_숫자"], errors="coerce").fillna(999)
    overall_rank = pd.to_numeric(hist_base["전체순위_숫자"], errors="coerce").fillna(999)
    is_standalone = (hist_base.get("노출형태", "") == "단독") | (hist_base["묶음_총개수"] <= 1)
    cond_st_top = is_standalone & (overall_rank <= 40)
    # 묶음: 3명 이하 1등만, 4명 이상 3등까지 (노션 정책)
    cond_bd_top = (~is_standalone) & (
        ((hist_base["묶음_총개수"] <= 3) & (rank_num == 1))
        | ((hist_base["묶음_총개수"] >= 4) & (rank_num <= 3))
    )
    base_top = cond_st_top | cond_bd_top
    # 48시간 강제 만료(ttl_ok) 및 30% 컷오프(rank_dead) 억지 로직 제거: base_top이면 상위권
    hist_base["is_top_tier"] = base_top.fillna(False)

    hist_base_real = hist_base.sort_values(key_cols + ["수집일시"]).copy()
    top_next = hist_base_real.groupby(key_cols, dropna=False)["is_top_tier"].shift(-1)
    death_points = hist_base_real[hist_base_real["is_top_tier"] & (top_next == False)][
        key_cols + ["수집일시"]
    ].rename(columns={"수집일시": "death_ts"})
    if not death_points.empty:
        death_points["death_ts"] = pd.to_datetime(death_points["death_ts"], errors="coerce")
        death_points = death_points.sort_values(
            key_cols + ["death_ts"], na_position="last", kind="mergesort"
        ).reset_index(drop=True)

    hist_base = hist_base_real
    hist_base = hist_base.sort_values(key_cols + ["수집일시"]).copy()

    hist_base["다음수집일시"] = hist_base.groupby(key_cols, dropna=False)["수집일시"].shift(-1)
    hist_base["구간분"] = (
        (hist_base["다음수집일시"] - hist_base["수집일시"])
        .dt.total_seconds()
        .div(60.0)
        .fillna(0.0)
        .clip(lower=0.0)
    )
    t0 = pd.to_datetime(hist_base["수집일시"], errors="coerce")
    t1 = pd.to_datetime(hist_base["다음수집일시"], errors="coerce")
    is_top_arr = hist_base["is_top_tier"].to_numpy(dtype=bool)
    t0_arr = t0.to_numpy()
    t1_arr = t1.to_numpy()
    top_mins_arr = np.zeros(len(hist_base), dtype=float)
    for i in range(len(hist_base)):
        if not is_top_arr[i]:
            continue
        a, b = pd.Timestamp(t0_arr[i]), pd.Timestamp(t1_arr[i])
        if pd.isna(a) or pd.isna(b) or b <= a:
            continue
        top_mins_arr[i] = _hours_excluding_daily_midnight_to_8am(a, b) * 60.0
    hist_base["상위권구간분"] = top_mins_arr
    hist_base["cum_top3_minutes"] = hist_base.groupby(key_cols, dropna=False)["상위권구간분"].cumsum()
    hist_base["cum_before_time"] = hist_base["cum_top3_minutes"] - hist_base["상위권구간분"]
    last_cum = hist_base.groupby(key_cols, dropna=False)["cum_top3_minutes"].last().reset_index(
        name="last_cum_top3"
    )

    timeline_df = _build_timeline_from_hist(hist_base, my_name_unified, my_hist, batch_end_ts, t_work)

    all_evt = b_work.dropna(subset=evt_cols).sort_values(key_cols + ["수집일시"]).copy()
    all_evt["수집일시"] = pd.to_datetime(all_evt["수집일시"], errors="coerce")
    if "묶음내순위_숫자" in all_evt.columns:
        all_evt["_rank_for_dedup"] = pd.to_numeric(all_evt["묶음내순위_숫자"], errors="coerce").fillna(999)
        all_evt = (
            all_evt.sort_values(key_cols + ["수집일시", "_rank_for_dedup"], kind="mergesort")
            .drop_duplicates(subset=key_cols + ["수집일시"], keep="first")
            .drop(columns=["_rank_for_dedup"])
            .copy()
        )
    all_evt["next_event_time"] = all_evt.groupby(key_cols)["수집일시"].shift(-1)
    all_evt["batch_end_ts"] = batch_end_ts

    if death_points.empty:
        all_evt["death_ts"] = pd.NaT
    else:
        all_evt = all_evt.dropna(subset=["수집일시"]).copy()
        death_points = death_points.dropna(subset=["death_ts"]).copy()
        for c in key_cols:
            if c in all_evt.columns:
                all_evt[c] = all_evt[c].astype(str)
            if c in death_points.columns:
                death_points[c] = death_points[c].astype(str)
        all_evt["수집일시"] = pd.to_datetime(all_evt["수집일시"], errors="coerce")
        death_points["death_ts"] = pd.to_datetime(death_points["death_ts"], errors="coerce")
        all_evt = all_evt.dropna(subset=["수집일시"]).copy()
        death_points = death_points.dropna(subset=["death_ts"]).copy()
        left_df = all_evt.sort_values("수집일시", na_position="last", kind="mergesort").reset_index(drop=True)
        right_df = death_points.sort_values("death_ts", na_position="last", kind="mergesort").reset_index(
            drop=True
        )
        left_df["수집일시"] = left_df["수집일시"].astype("datetime64[ns]")
        right_df["death_ts"] = right_df["death_ts"].astype("datetime64[ns]")
        all_evt = pd.merge_asof(
            left_df,
            right_df,
            by=key_cols,
            left_on="수집일시",
            right_on="death_ts",
            direction="forward",
        )

    end_src = pd.concat(
        [all_evt["next_event_time"], all_evt["death_ts"], all_evt["batch_end_ts"]],
        axis=1,
    )
    all_evt["next_event_time"] = pd.to_datetime(all_evt["next_event_time"], errors="coerce")
    all_evt["death_ts"] = pd.to_datetime(all_evt["death_ts"], errors="coerce")
    all_evt["batch_end_ts"] = pd.to_datetime(all_evt["batch_end_ts"], errors="coerce")
    all_evt["effective_end_ts"] = pd.to_datetime(end_src.min(axis=1), errors="coerce")
    all_evt["effective_end_ts"] = all_evt["effective_end_ts"].fillna(all_evt["수집일시"])
    all_evt["effective_end_ts"] = pd.to_datetime(all_evt["effective_end_ts"], errors="coerce")

    hist_cum = hist_base[key_cols + ["수집일시", "cum_before_time"]].copy()
    all_evt = all_evt.merge(
        hist_cum.rename(columns={"cum_before_time": "start_cum"}),
        on=key_cols + ["수집일시"],
        how="left",
    )
    all_evt = all_evt.merge(last_cum, on=key_cols, how="left")
    _hist_end = hist_cum.rename(columns={"수집일시": "end_point_ts", "cum_before_time": "end_cum"})
    _hist_end["end_point_ts"] = pd.to_datetime(_hist_end["end_point_ts"], errors="coerce")
    all_evt = all_evt.dropna(subset=["effective_end_ts"]).copy()
    _hist_end = _hist_end.dropna(subset=["end_point_ts"]).copy()
    for c in key_cols:
        if c in all_evt.columns:
            all_evt[c] = all_evt[c].astype(str)
        if c in _hist_end.columns:
            _hist_end[c] = _hist_end[c].astype(str)
    all_evt["effective_end_ts"] = pd.to_datetime(all_evt["effective_end_ts"], errors="coerce")
    _hist_end["end_point_ts"] = pd.to_datetime(_hist_end["end_point_ts"], errors="coerce")
    all_evt = all_evt.dropna(subset=["effective_end_ts"]).copy()
    _hist_end = _hist_end.dropna(subset=["end_point_ts"]).copy()
    left_df = all_evt.sort_values("effective_end_ts", na_position="last", kind="mergesort").reset_index(drop=True)
    right_df = _hist_end.sort_values("end_point_ts", na_position="last", kind="mergesort").reset_index(drop=True)
    left_df["effective_end_ts"] = left_df["effective_end_ts"].astype("datetime64[ns]")
    right_df["end_point_ts"] = right_df["end_point_ts"].astype("datetime64[ns]")
    all_evt = pd.merge_asof(
        left_df,
        right_df,
        left_on="effective_end_ts",
        right_on="end_point_ts",
        by=key_cols,
        direction="backward",
    )
    all_evt["start_cum"] = all_evt["start_cum"].fillna(0.0)
    all_evt["end_cum"] = all_evt["end_cum"].where(all_evt["effective_end_ts"].notna(), all_evt["last_cum_top3"])
    all_evt["end_cum"] = all_evt["end_cum"].fillna(all_evt["start_cum"])
    all_evt["hold_minutes"] = (all_evt["end_cum"] - all_evt["start_cum"]).clip(lower=0.0)
    all_evt = all_evt.drop(columns=["last_cum_top3", "end_point_ts"], errors="ignore")

    my_evt = all_evt[all_evt["부동산명_통합"] == my_name_unified].dropna(subset=["매물묶음키"]).copy()
    my_evt["평형키"] = my_evt["층/타입"].map(_extract_area_key)
    target_pairs_df = my_evt[["단지명", "평형키"]].dropna().drop_duplicates()

    market_evt = all_evt.dropna(subset=["매물묶음키", "단지명", "층/타입"]).copy()
    market_evt["평형키"] = market_evt["층/타입"].map(_extract_area_key)
    if not target_pairs_df.empty:
        market_evt = market_evt.merge(target_pairs_df, on=["단지명", "평형키"], how="inner")
    bench = (
        market_evt.groupby(["단지명", "평형키"], dropna=False)["hold_minutes"]
        .mean()
        .reset_index(name="avg_hold_minutes")
    )
    my_evt = my_evt.merge(bench, on=["단지명", "평형키"], how="left")
    my_evt["avg_hold_minutes"] = my_evt["avg_hold_minutes"].fillna(0.0)
    my_evt["is_value"] = (
        ((my_evt["avg_hold_minutes"] > 0) & (my_evt["hold_minutes"] > my_evt["avg_hold_minutes"]))
        | ((my_evt["avg_hold_minutes"] <= 0) & (my_evt["hold_minutes"] > 10))
    )
    my_evt["is_waste"] = (
        (my_evt["hold_minutes"] <= 10)
        | ((my_evt["avg_hold_minutes"] > 0) & (my_evt["hold_minutes"] < (my_evt["avg_hold_minutes"] * 0.5)))
    )

    if not my_evt.empty:
        my_evt_unique = my_evt.drop_duplicates(subset=["매물묶음키", "수집일시", "확인일자"])
        renew_series = my_evt_unique.groupby("매물묶음키").size()
        renew_map_14d = renew_series.astype(int).to_dict()
    else:
        renew_map_14d = {}

    if not my_evt.empty:
        latest_evt_by_bundle = (
            my_evt.sort_values("수집일시").drop_duplicates("매물묶음키", keep="last").set_index("매물묶음키")
        )
        hold_by_bundle = latest_evt_by_bundle["hold_minutes"]
    else:
        hold_by_bundle = pd.Series(dtype="float64")

    hist_my_real = hist_base_real[hist_base_real["부동산명_통합"] == my_name_unified].copy()
    if not hist_my_real.empty:
        last_rows_my = hist_my_real.sort_values("수집일시").groupby("매물묶음키", dropna=False).tail(1)
        last_is_top_map = (
            last_rows_my.set_index("매물묶음키")["is_top_tier"].fillna(False).astype(bool).to_dict()
        )
        last_top_ts_map = (
            hist_my_real[hist_my_real["is_top_tier"]]
            .groupby("매물묶음키", dropna=False)["수집일시"]
            .max()
            .to_dict()
        )
    else:
        last_is_top_map = {}
        last_top_ts_map = {}

    master_strategy_dict = precalculate_ai_strategy(
        trk, boosted_df, filter_realtor_name, comp_df
    )

    # [초고속 최적화] 매물묶음키별 갱신 건수: per-key for문 대신 벡터화
    market_keys = b_work["매물묶음키"].dropna().unique().tolist()
    bw_sub = b_work.copy()
    bw_sub["_ts"] = pd.to_datetime(bw_sub["수집일시"], errors="coerce")
    bw_sub = filter_exclude_sunday_rows(bw_sub, "_ts")
    dedup_cols = [c for c in ["매물묶음키", "_ts", "부동산명", "확인일자"] if c in bw_sub.columns]
    bw_sub = bw_sub.drop_duplicates(subset=dedup_cols)
    renew_counts_series = bw_sub.groupby("매물묶음키").size()
    market_freq = pd.DataFrame({"매물묶음키": market_keys})
    market_freq["광고 갱신 횟수"] = (
        market_freq["매물묶음키"].map(renew_counts_series).fillna(0).astype(int)
    )
    if not market_freq.empty:
        market_freq["pct"] = market_freq["광고 갱신 횟수"].rank(pct=True, method="average")
    else:
        market_freq["pct"] = pd.Series(dtype="float")
    freq_map = market_freq.set_index("매물묶음키")["광고 갱신 횟수"].to_dict()
    pct_map = market_freq.set_index("매물묶음키")["pct"].to_dict()

    last_trigger_map = (
        b_work.dropna(subset=["매물묶음키", "수집일시"])
        .groupby("매물묶음키")["수집일시"]
        .max()
        .to_dict()
    )

    latest_my = my_hist.sort_values("수집일시").drop_duplicates("매물묶음키", keep="last")
    spec_src = my_hist.sort_values("수집일시").copy()
    for c in ["단지명", "동/호수", "층/타입", "거래방식", "가격", "방향"]:
        if c not in spec_src.columns:
            spec_src[c] = ""
        spec_src[c] = (
            spec_src[c]
            .astype(str)
            .str.strip()
            .replace({"nan": "", "None": "", "": pd.NA})
        )
    spec_grouped = spec_src.groupby("매물묶음키", dropna=False).last().fillna("")
    spec_map = spec_grouped[["단지명", "동/호수", "층/타입", "거래방식", "가격", "방향"]].to_dict(
        orient="index"
    )
    value_by_bundle = my_evt.groupby("매물묶음키")["is_value"].sum() if not my_evt.empty else pd.Series(dtype="int64")
    waste_by_bundle = my_evt.groupby("매물묶음키")["is_waste"].sum() if not my_evt.empty else pd.Series(dtype="int64")

    rows = []
    for row in latest_my.itertuples(index=False):
        bkey = getattr(row, "매물묶음키")
        renew_cnt = int(renew_map_14d.get(bkey, 0))
        pct = float(pct_map.get(bkey, 0.0))
        if pct >= 0.7:
            importance = "상"
        elif pct >= 0.4:
            importance = "중"
        else:
            importance = "하"
        spec = spec_map.get(bkey, {})
        danji = str(spec.get("단지명", "") or getattr(row, "단지명", ""))
        dongho = str(spec.get("동/호수", "") or getattr(row, "동/호수", ""))
        floor_type = _dedup_floor_type_text(str(spec.get("층/타입", "") or getattr(row, "층/타입", "")))
        deal_type = str(spec.get("거래방식", "") or getattr(row, "거래방식", ""))
        direction = str(spec.get("방향", "") or getattr(row, "방향", "") or _extract_direction_text(floor_type))
        raw_price_src = spec.get("가격") or getattr(row, "가격", None)
        price_str = _fmt_price_kr(raw_price_src)
        is_single_ui = (int(global_b_counts.get(bkey, 2)) <= 1) or (str(getattr(row, "노출형태", "")) == "단독")
        price = f"{price_str} 💎[단독]" if is_single_ui else price_str
        base_hold_str = _fmt_minutes_as_hm(hold_by_bundle.get(bkey, 0.0))
        overall_rank_val = getattr(row, "전체순위_숫자", "")
        if is_single_ui and pd.notna(overall_rank_val) and str(overall_rank_val).strip() != "":
            hold_display = f"{base_hold_str} (전체 {int(float(overall_rank_val))}위)"
        else:
            hold_display = base_hold_str
        is_defending = bool(last_is_top_map.get(bkey, False))
        final_ts = batch_end_ts if is_defending else last_top_ts_map.get(bkey, pd.NaT)
        final_ts_str = final_ts.strftime("%m/%d %H:%M") if pd.notna(final_ts) else "—"
        status_str = "✅ 방어 중" if is_defending else "❌ 효력 종료"
        w_cnt = int(waste_by_bundle.get(bkey, 0))
        h_min = float(hold_by_bundle.get(bkey, 0.0))
        lt_raw = last_trigger_map.get(bkey)
        if lt_raw is not None and pd.notna(lt_raw):
            recent_update_str = pd.Timestamp(lt_raw).strftime("%m/%d %H:%M")
        else:
            recent_update_str = "기록 없음"
        rows.append(
            {
                "단지명": danji,
                "Task": _task_label_from_spec(danji, dongho, floor_type, raw_price_src, direction),
                "매물 중요도": importance,
                "매물명": f"{danji} | {dongho} | {floor_type} | {deal_type} | {price}".strip(" |"),
                "광고 갱신 횟수": renew_cnt,
                "상위권 유지 기간": hold_display,
                "최종 효력 유지 시각": final_ts_str,
                "최근 갱신 시각": recent_update_str,
                "상태": status_str,
                "Value / Waste 횟수": f"{int(value_by_bundle.get(bkey, 0))} / {w_cnt}",
                "Waste 횟수": w_cnt,
                "hold_minutes_raw": h_min,
                "광고 추천 시간": master_strategy_dict.get(bkey, "✅ 자유 갱신"),
            }
        )

    action_df = pd.DataFrame(rows)
    if action_df.empty:
        return _empty_action_df(), timeline_df if not timeline_df.empty else _empty_timeline_df()
    action_df = action_df.sort_values(["매물 중요도", "광고 갱신 횟수"], ascending=[True, False]).reset_index(
        drop=True
    )
    return action_df, timeline_df


def compute_prime_action_df(
    trk: pd.DataFrame,
    boosted_df: pd.DataFrame,
    filter_realtor_name: str,
    comp_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """프라임 데이터 계산 (`_build_prime_action_df`에 @st.cache_data 적용)."""
    return _build_prime_action_df(trk, boosted_df, filter_realtor_name, comp_df)


def _precompute_all_complexes_data_impl(
    df_to_process: pd.DataFrame,
    complexes_list: list[str],
    realtor_name: str,
    target_date: datetime.date,
) -> dict[str, dict[str, pd.DataFrame]]:
    """단지별 사전 계산 본체 (캐시 없음). 미인증 데모는 이 경로만 호출해 대용량 캐시 키·스피너를 피한다."""
    import time  # 상단에 임포트했지만 혹시 몰라 안전하게 내부에서도 확인

    start_t = time.time()
    print(
        f"\n[START] precompute_all_complexes_data (대상 단지 수: {len(complexes_list)}개)"
    )

    results: dict[str, dict[str, pd.DataFrame]] = {}
    for comp in complexes_list:
        t_df = df_to_process[df_to_process["단지명"] == comp].copy()
        if t_df.empty:
            continue
        for col in t_df.select_dtypes(include=["object", "string"]).columns:
            s = t_df[col].astype(str)
            s = s.str.replace("\\", "/", regex=False)
            s = s.str.replace("\ufffd", "", regex=False)
            s = s.str.replace("<", "(", regex=False)
            s = s.str.replace(">", ")", regex=False)
            t_df[col] = s
        trk = build_listing_tracking_keys(t_df, time_col="수집일시")
        if "CP사" not in trk.columns:
            trk["CP사"] = ""
        trk["CP사"] = trk["CP사"].fillna("").astype(str).str.strip()
        # 1. 부동산명 정제 및 날짜 점(.) 찌꺼기 제거
        trk["부동산명_정제"] = trk["부동산명"].apply(clean_realtor_name)
        conf_s = trk["확인일자"].astype(str).str.strip().str.rstrip(".")
        trk["확인일자_dt"] = pd.to_datetime(conf_s, format="%y.%m.%d", errors="coerce")
        na_m = trk["확인일자_dt"].isna() & (conf_s != "") & (conf_s.str.lower() != "nan")
        if na_m.any():
            trk.loc[na_m, "확인일자_dt"] = pd.to_datetime(conf_s[na_m], errors="coerce")

        # 2. 매물을 특정하는 절대 기준 세팅 (대표님 엑셀 SOP 완벽 이식)
        # 노출형태(단독/묶음)는 갱신 시 변할 수 있으므로 제거, 멀티 채널 핑퐁 방지를 위해 CP사 추가
        trk = _ensure_direction_and_tracking_key(trk)
        grp_keys = ["부동산명_정제", "단지명", "동/호수", "층/타입", "거래방식", "가격", "CP사", "방향"]

        for c in grp_keys:
            if c not in trk.columns:
                trk[c] = "미상"
            trk[c] = trk[c].fillna("미상")

        trk = trk.sort_values(grp_keys + ["수집일시"])

        # 3. 대표님 엑셀 M열(고유번호) 비교 로직 적용
        # 확인일자(날짜) 비교 대신, 가장 확실한 갱신 증거인 '고유번호'의 변경을 추적
        if "고유번호" not in trk.columns:
            trk["고유번호"] = trk["매물번호"]  # 혹시 컬럼명이 다를 경우를 대비한 방어 코드

        trk["prev_고유번호"] = trk.groupby(grp_keys, dropna=False)["고유번호"].shift(1)

        # 4. 동일매물(스펙+가격+CP사 완벽 일치) 내에서 고유번호(M열)가 달라진 순간 갱신 포착
        c1 = (
            trk["고유번호"].notna()
            & trk["prev_고유번호"].notna()
            & (trk["고유번호"] != trk["prev_고유번호"])
        )
        boosted_df = trk[c1].copy()

        # --- [추가] 탭 4: 시장 점유율 및 타사 패턴 사전 계산 ---
        # comp_df(오늘 요일 마지노선)을 먼저 구축한 뒤 AI 추천과 동일한 '뇌'로 전달
        _ms_cols = ["단지명", "동/호수", "층/타입", "거래방식", "가격", "부동산명", "묶음내순위"]
        if not all(c in t_df.columns for c in _ms_cols):
            ms_df = pd.DataFrame(columns=["부동산명", "매물건수", "총점수"])
            comp_df = pd.DataFrame(columns=["부동산명", "총횟수", "갱신빈도"])
        else:
            t_df_ms = t_df.copy()
            if "CP사" not in t_df_ms.columns:
                t_df_ms["CP사"] = ""
            t_df_ms["CP사"] = t_df_ms["CP사"].fillna("").astype(str).str.strip()
            t_df_ms = _ensure_direction_and_tracking_key(t_df_ms)
            t_df_ms["부동산명_정제"] = t_df_ms["부동산명"].apply(clean_realtor_name)
            t_df_ms["_순위정렬"] = pd.to_numeric(
                t_df_ms["묶음내순위"]
                .astype(str)
                .str.replace("단독", "1", regex=False)
                .str.replace(r"[^0-9]", "", regex=True),
                errors="coerce",
            ).fillna(999)

            # [수정] 순위가 높은(숫자가 작은) 순으로 정렬 후 CP사·부동산명 기준 중복 제거
            uniq = t_df_ms.sort_values("_순위정렬").drop_duplicates(
                subset=[
                    "단지명",
                    "동/호수",
                    "층/타입",
                    "거래방식",
                    "가격",
                    "부동산명_정제",
                    "CP사",
                    "방향",
                ]
            ).copy()

            uniq["묶음_총개수"] = uniq.groupby(
                ["단지명", "동/호수", "층/타입", "거래방식", "가격", "CP사", "방향"]
            )["부동산명_정제"].transform("count")

            uniq["파워점수"] = 10 + (10 / uniq["_순위정렬"]) + (uniq["묶음_총개수"] * 0.1)

            ms_df = (
                uniq.groupby("부동산명_정제", dropna=False)
                .agg(매물건수=("부동산명_정제", "count"), 총점수=("파워점수", "sum"))
                .reset_index()
                .rename(columns={"부동산명_정제": "부동산명"})
            )
            ms_df["총점수"] = ms_df["총점수"].round().astype(int)

            _ts_all = pd.to_datetime(df_to_process["수집일시"], errors="coerce")
            if _ts_all.notna().any():
                _dmin, _dmax = _ts_all.min().date(), _ts_all.max().date()
                analysis_days = max(1, (_dmax - _dmin).days + 1)
            else:
                analysis_days = 1

            b_df_comp = boosted_df.copy()
            b_df_comp["부동산명_정제"] = b_df_comp["부동산명"].apply(clean_realtor_name)
            b_df_comp["수집일시"] = pd.to_datetime(b_df_comp["수집일시"], errors="coerce")

            def _calc_renew_freq(s: pd.Series) -> str:
                s = pd.to_datetime(s, errors="coerce").dropna()
                if s.empty:
                    return "알수없음"
                active_days = s.dt.normalize().nunique()
                if active_days == 0:
                    return "알수없음"
                freq = analysis_days / active_days
                if freq <= 1.3:
                    return "🔥 매일 갱신"
                if freq <= 2.5:
                    return "⚡ 2일에 1번"
                if freq <= 4.0:
                    return "🚶 3~4일에 1번"
                if freq <= 8.0:
                    return "🐢 주 1~2회"
                return "💤 비정기적 (월 1~2회)"

            comp_df = (
                b_df_comp.dropna(subset=["부동산명_정제"])
                .groupby("부동산명_정제", dropna=False)
                .agg(총횟수=("부동산명_정제", "count"), 갱신빈도=("수집일시", _calc_renew_freq))
                .reset_index()
                .rename(columns={"부동산명_정제": "부동산명"})
                .sort_values("총횟수", ascending=False)
            )

            # [추가] 주력 갱신 시간, 요일 그룹별 주력·마지노선, 예측 신뢰도
            # target_date: 달력 종료일(e_d)과 동기 — 실시간 서버 시각이 아님
            _pat_wd = target_date.weekday()

            def _today_weekday_group_kr() -> str:
                if _pat_wd == 0:
                    return "월요일"
                if _pat_wd == 4:
                    return "금요일"
                if _pat_wd in (5, 6):
                    return "주말"
                return "화~목"

            def _filter_same_weekday_bucket(s: pd.Series) -> pd.Series:
                wd = s.dt.weekday
                if _pat_wd == 0:
                    return s[wd == 0]
                if _pat_wd == 4:
                    return s[wd == 4]
                if _pat_wd in (5, 6):
                    return s[wd.isin([5, 6])]
                return s[wd.isin([1, 2, 3])]

            def _peak_str_and_deadline(s_dt: pd.Series) -> tuple[str, int]:
                if s_dt.empty:
                    return "-", 18
                hours = s_dt.dt.hour
                top_hours = hours.value_counts().head(2)
                idx_sorted = sorted(int(h) for h in top_hours.index.tolist())
                if not idx_sorted:
                    return "-", 18
                if len(idx_sorted) == 1:
                    h0 = idx_sorted[0]
                    return f"{h0:02d}시", min(h0 + 1, 23)
                h0, h1 = idx_sorted[0], idx_sorted[1]
                peak_str = f"{h0}~{h1}시"
                deadline = max(h0, h1) + 1
                return peak_str, min(deadline, 23)

            def _get_pattern_details(group):
                s_dt = pd.to_datetime(group["수집일시"], errors="coerce").dropna()
                total = len(s_dt)
                wd_label = _today_weekday_group_kr()
                if total == 0:
                    return pd.Series(
                        {
                            "주력 갱신 시간": "-",
                            "예측 신뢰도": "-",
                            "오늘_요일_그룹": wd_label,
                            "오늘 요일 주력 시간": "-",
                            "오늘 요일 마지노선": 18,
                            "오늘요일_실측": False,
                        }
                    )

                peak_all, deadline_all = _peak_str_and_deadline(s_dt)
                hours = s_dt.dt.hour
                top_hours = hours.value_counts().head(2)
                rel_pct = (top_hours.sum() / total) * 100

                if rel_pct >= 60:
                    rel_str = f"🟢 높음 ({rel_pct:.0f}%)"
                elif rel_pct >= 30:
                    rel_str = f"🟡 보통 ({rel_pct:.0f}%)"
                else:
                    rel_str = f"🔴 낮음 ({rel_pct:.0f}%)"

                s_wd = _filter_same_weekday_bucket(s_dt)
                weekday_has_sample = len(s_wd) > 0
                if not weekday_has_sample:
                    peak_wd, deadline_wd = peak_all, deadline_all
                else:
                    peak_wd, deadline_wd = _peak_str_and_deadline(s_wd)

                return pd.Series(
                    {
                        "주력 갱신 시간": peak_all,
                        "예측 신뢰도": rel_str,
                        "오늘_요일_그룹": wd_label,
                        "오늘 요일 주력 시간": peak_wd,
                        "오늘 요일 마지노선": int(deadline_wd),
                        "오늘요일_실측": weekday_has_sample,
                    }
                )

            pattern_df = (
                b_df_comp.dropna(subset=["부동산명_정제"])
                .groupby("부동산명_정제")
                .apply(_get_pattern_details, include_groups=False)
                .reset_index()
                .rename(columns={"부동산명_정제": "부동산명"})
            )

            # 기존 comp_df에 계산된 패턴 데이터 병합
            comp_df = comp_df.merge(pattern_df, on="부동산명", how="left").sort_values(
                "총횟수", ascending=False
            )

        act_df, tl_df = compute_prime_action_df(trk, boosted_df, realtor_name, comp_df)

        strat_by_task: dict[str, object] = {}
        if not act_df.empty and "Task" in act_df.columns and "광고 추천 시간" in act_df.columns:
            for _, _r in act_df.iterrows():
                _tk = str(_r.get("Task", "")).strip()
                if _tk:
                    strat_by_task[_tk] = _r.get("광고 추천 시간")

        results[comp] = {
            "action": act_df,
            "timeline": tl_df,
            "ms": ms_df,
            "comp": comp_df,
            "boosted": boosted_df,
            "strategy_dict": strat_by_task,
        }

    elapsed = time.time() - start_t
    print(f"[DONE] precompute_all_complexes_data 완료 ({elapsed:.2f}s)\n")
    return results


@st.cache_data(show_spinner="🚀 선택된 기간의 모든 단지 데이터를 미리 계산 중입니다... (최초 1회만 소요)")
def precompute_all_complexes_data(
    df_to_process: pd.DataFrame,
    complexes_list: list[str],
    realtor_name: str,
    target_date: datetime.date,
) -> dict[str, dict[str, pd.DataFrame]]:
    """기간·부동산 필터가 같을 때 단지 전환 시 재계산 없이 쓰기 위한 일괄 사전 계산 (캐시 적용)."""
    return _precompute_all_complexes_data_impl(
        df_to_process, complexes_list, realtor_name, target_date
    )


def _unauth_demo_listing_specs() -> list[dict[str, str | int]]:
    """미인증 데모 내 매물 20건(상위권 15 / 탈락 5) — 동·호는 '행복동 사랑단지 101동 1502호' 형식의 동/호수 문자열."""
    return [
        {"dong": "101동 1502호", "floor": "99/74m² | 고/29층 | 남향", "price": "950000000", "bundle": "1", "overall": "8", "hold": "18.2시간", "eve2": "20:08", "renew": 52, "last_up": "05/14 15:03"},
        {"dong": "101동 1503호", "floor": "84A/84A | 중/22층 | 남향", "price": "882000000", "bundle": "2", "overall": "11", "hold": "14.6시간", "eve2": "19:44", "renew": 48, "last_up": "05/14 15:11"},
        {"dong": "101동 1505호", "floor": "84B/84B | 저/15층 | 동향", "price": "895000000", "bundle": "1", "overall": "14", "hold": "19.5시간", "eve2": "20:31", "renew": 51, "last_up": "05/14 15:02"},
        {"dong": "102동 1201호", "floor": "113B/84B | 12/29층 | 서향", "price": "968000000", "bundle": "3", "overall": "6", "hold": "16.9시간", "eve2": "19:39", "renew": 45, "last_up": "05/14 15:19"},
        {"dong": "102동 1203호", "floor": "84A/84A | 고/30층 | 남서향", "price": "935000000", "bundle": "2", "overall": "19", "hold": "12.0시간", "eve2": "21:01", "renew": 41, "last_up": "05/14 15:06"},
        {"dong": "103동 803호", "floor": "59A/59A | 중/18층 | 북향", "price": "745000000", "bundle": "1", "overall": "22", "hold": "21.3시간", "eve2": "20:52", "renew": 54, "last_up": "05/14 15:14"},
        {"dong": "103동 905호", "floor": "113A/84A | 8/35층 | 남향", "price": "978000000", "bundle": "1", "overall": "9", "hold": "17.4시간", "eve2": "19:56", "renew": 49, "last_up": "05/14 15:08"},
        {"dong": "104동 1402호", "floor": "84B/84B | 20/28층 | 남동향", "price": "905000000", "bundle": "2", "overall": "16", "hold": "13.8시간", "eve2": "20:17", "renew": 43, "last_up": "05/14 15:21"},
        {"dong": "104동 1404호", "floor": "152A/152A | 24/35층 | 남향", "price": "1320000000", "bundle": "2", "overall": "25", "hold": "11.8시간", "eve2": "21:16", "renew": 39, "last_up": "05/14 15:04"},
        {"dong": "105동 602호", "floor": "84A/84A | 14/22층 | 동향", "price": "888000000", "bundle": "3", "overall": "28", "hold": "15.7시간", "eve2": "19:33", "renew": 47, "last_up": "05/14 15:16"},
        {"dong": "105동 608호", "floor": "113A/84A | 저/24층 | 남향", "price": "928000000", "bundle": "1", "overall": "12", "hold": "20.1시간", "eve2": "20:46", "renew": 50, "last_up": "05/14 15:09"},
        {"dong": "106동 2201호", "floor": "84A/84A | 중/11층 | 남향", "price": "902000000", "bundle": "2", "overall": "31", "hold": "10.5시간", "eve2": "20:03", "renew": 38, "last_up": "05/14 15:22"},
        {"dong": "106동 2205호", "floor": "84B/84B | 고/27층 | 서향", "price": "916000000", "bundle": "1", "overall": "18", "hold": "21.9시간", "eve2": "19:48", "renew": 55, "last_up": "05/14 15:01"},
        {"dong": "107동 1108호", "floor": "59A/59A | 저/6층 | 동향", "price": "738000000", "bundle": "1", "overall": "33", "hold": "9.7시간", "eve2": "21:22", "renew": 36, "last_up": "05/14 15:18"},
        {"dong": "107동 1110호", "floor": "113A/84A | 중/31층 | 남동향", "price": "989000000", "bundle": "2", "overall": "35", "hold": "14.3시간", "eve2": "20:25", "renew": 44, "last_up": "05/14 15:12"},
        {"dong": "108동 3305호", "floor": "84B/84B | 5/21층 | 북향", "price": "871000000", "bundle": "4", "overall": "44", "hold": "1.3시간 (전체 44위)", "eve2": "20:12", "renew": 28, "last_up": "05/14 09:14"},
        {"dong": "109동 902호", "floor": "59A/59A | 중/14층 | 남향", "price": "752000000", "bundle": "5", "overall": "47", "hold": "0.8시간 (전체 47위)", "eve2": "19:37", "renew": 26, "last_up": "05/14 08:52"},
        {"dong": "109동 905호", "floor": "113B/84B | 저/19층 | 서향", "price": "941000000", "bundle": "6", "overall": "49", "hold": "1.6시간 (전체 49위)", "eve2": "21:07", "renew": 31, "last_up": "05/14 10:03"},
        {"dong": "110동 802호", "floor": "152A/152A | 30/35층 | 남향", "price": "1288000000", "bundle": "7", "overall": "52", "hold": "1.0시간 (전체 52위)", "eve2": "20:38", "renew": 29, "last_up": "05/14 09:41"},
        {"dong": "110동 806호", "floor": "84A/84A | 저/8층 | 동향", "price": "865000000", "bundle": "8", "overall": "46", "hold": "1.5시간 (전체 46위)", "eve2": "19:45", "renew": 27, "last_up": "05/14 09:28"},
    ]


def _build_unauth_demo_raw_df() -> pd.DataFrame:
    """미인증 전용 스냅샷: 내 매물 20건(타임라인 20축) + 3501동 번들 경쟁사 4곳(감시망)."""
    KST = timezone(timedelta(hours=9))
    today = datetime.now(KST).date()
    danji = "행복동 사랑단지"
    deal = "매매"
    cp = "네이버부동산"
    my_agency = "사랑공인중개사사무소"

    specs = _unauth_demo_listing_specs()
    if len(specs) != 20:
        raise RuntimeError("미인증 데모 매물 스펙은 정확히 20건이어야 합니다.")

    competitors: list[tuple[str, str, str]] = [
        ("베스트중개부동산", "6", "2"),
        ("행복공인부동산", "7", "3"),
        ("탑랭크중개부동산", "8", "4"),
        ("미래스토리공인중개사", "9", "5"),
    ]

    dong0 = str(specs[0]["dong"])
    floor0 = str(specs[0]["floor"])
    price0 = str(specs[0]["price"])

    rows: list[dict] = []
    seq = 0

    def _row(
        ts: datetime,
        *,
        dong: str,
        floor_t: str,
        price: str,
        overall: str,
        bundle: str,
        agency: str,
        conf: str,
        uid: str,
        exposure: str = "단독",
    ) -> None:
        rows.append(
            {
                "수집일시": ts,
                "단지명": danji,
                "전체순위": overall,
                "묶음내순위": bundle,
                "동/호수": dong,
                "층/타입": floor_t,
                "거래방식": deal,
                "가격": price,
                "확인일자": conf,
                "부동산명": agency,
                "CP사": cp,
                "고유번호": uid,
                "노출형태": exposure,
            }
        )

    for day_i in range(-26, 1):
        d = today + timedelta(days=day_i)
        conf = d.strftime("%y.%m.%d")
        for sp in specs:
            dong = str(sp["dong"])
            floor_t = str(sp["floor"])
            price = str(sp["price"])
            bundle = str(sp["bundle"])
            overall = str(sp["overall"])
            for slot, (hour, minute) in enumerate(((9, 12), (15, 3))):
                seq += 1
                ts = datetime.combine(d, datetime.min.time()) + timedelta(
                    hours=hour, minutes=minute + (seq % 10), seconds=seq % 50
                )
                alt = "M" if (day_i + slot + hash(dong)) % 2 == 0 else "N"
                uid = f"M-{dong}-{day_i}-{slot}-{seq}-{alt}"
                _row(ts, dong=dong, floor_t=floor_t, price=price, overall=overall, bundle=bundle, agency=my_agency, conf=conf, uid=uid)

    for day_i in range(-26, 0):
        d = today + timedelta(days=day_i)
        conf = d.strftime("%y.%m.%d")
        for ci, (ag, overall, bundle) in enumerate(competitors):
            for slot, (hour, minute) in enumerate(((10, 1), (14, 40 + ci))):
                seq += 1
                ts = datetime.combine(d, datetime.min.time()) + timedelta(
                    hours=hour, minutes=minute + (seq % 7), seconds=seq % 45
                )
                alt = "P" if (day_i + slot + ci) % 2 == 0 else "Q"
                uid = f"C{ci}-{day_i}-{slot}-{alt}"
                _row(
                    ts,
                    dong=dong0,
                    floor_t=floor0,
                    price=price0,
                    overall=overall,
                    bundle=bundle,
                    agency=ag,
                    conf=conf,
                    uid=uid,
                )

    conf_t = today.strftime("%y.%m.%d")
    # 오늘 스냅: 베스트중개(11:20)·미래스토리(14:50)만 당일 갱신 — 행복/탑랭크는 전일까지(대기·출혈 연출).
    today_pairs = [
        (0, (9, 5), (11, 20)),
        (3, (14, 12), (14, 50)),
    ]
    for ci, hm0, hm1 in today_pairs:
        ag, overall, bundle = competitors[ci]
        uid0 = f"C{ci}-T0-{hm0[0]}{hm0[1]:02d}"
        uid1 = f"C{ci}-T1-{hm1[0]}{hm1[1]:02d}"
        _row(
            datetime.combine(today, datetime.min.time()) + timedelta(hours=hm0[0], minutes=hm0[1]),
            dong=dong0,
            floor_t=floor0,
            price=price0,
            overall=overall,
            bundle=bundle,
            agency=ag,
            conf=conf_t,
            uid=uid0,
        )
        _row(
            datetime.combine(today, datetime.min.time()) + timedelta(hours=hm1[0], minutes=hm1[1]),
            dong=dong0,
            floor_t=floor0,
            price=price0,
            overall=overall,
            bundle=bundle,
            agency=ag,
            conf=conf_t,
            uid=uid1,
        )

    return pd.DataFrame(rows)


def _unauth_ms_top10_df() -> pd.DataFrame:
    """미인증 화면 전용 M/S Top 10 표·차트 데이터(총점수=점유율 %)."""
    data = [
        ("사랑공인중개사사무소", 31, 22.4),
        ("행복공인부동산", 24, 18.1),
        ("단지앞365부동산", 19, 13.5),
        ("e편한로얄공인중개사", 18, 12.8),
        ("한강뷰스카이공인", 14, 8.2),
        ("프라임단지중개", 12, 7.0),
        ("드림하우스공인", 11, 5.4),
        ("로얄OK부동산", 9, 4.5),
        ("오늘부동산단지점", 8, 3.1),
        ("탑랭크알파공인", 6, 2.0),
    ]
    return pd.DataFrame(data, columns=["부동산명", "매물건수", "총점수"])


def _patch_unauth_demo_timeline_realistic(complex_data: dict[str, object], chart_day: datetime.date) -> None:
    """미인증: 간트 막대가 48시간 창을 가득 채우지 않도록 매물별 Start/Finish를 짧은 구간으로 재구성."""
    tl = complex_data.get("timeline")
    if not isinstance(tl, pd.DataFrame) or tl.empty or "Task" not in tl.columns:
        return
    if "Start" not in tl.columns or "Finish" not in tl.columns:
        return
    day_start = pd.Timestamp(datetime.combine(chart_day - timedelta(days=1), datetime.min.time()))
    day_end = (
        pd.Timestamp(datetime.combine(chart_day, datetime.min.time()))
        + pd.Timedelta(days=1)
        - pd.Timedelta(seconds=1)
    )
    work = tl.copy()
    work["Start"] = pd.to_datetime(work["Start"], errors="coerce")
    work["Finish"] = pd.to_datetime(work["Finish"], errors="coerce")
    vis = work[(work["Finish"] > day_start) & (work["Start"] < day_end)].copy()
    if vis.empty:
        return

    rows_out: list[dict] = []
    tasks_sorted = sorted(vis["Task"].dropna().unique().tolist(), key=str)
    for ti, task in enumerate(tasks_sorted):
        sub = vis[vis["Task"] == task]
        state = str(sub["State"].iloc[-1]) if "State" in sub.columns and len(sub) else "🟢 1~3위 방어 중"
        rank = sub["내순위"].iloc[-1] if "내순위" in sub.columns and len(sub) else "—"
        top1 = sub["Top1부동산"].iloc[-1] if "Top1부동산" in sub.columns and len(sub) else "—"
        seed = (abs(hash(str(task))) % (2**31 - 1) + int(chart_day.strftime("%Y%m%d")) + ti * 97) % (2**31 - 1)
        rng = np.random.RandomState(seed)
        n_seg = 1 + (ti % 3)
        day_y = chart_day - timedelta(days=1)
        day_t = chart_day
        for s in range(n_seg):
            use_y = (ti + s) % 2 == 0
            d = day_y if use_y else day_t
            h0 = int(rng.randint(8, 20))
            m0 = int(rng.randint(0, 59))
            dur_m = int(rng.randint(35, 220))
            t0 = pd.Timestamp(datetime.combine(d, datetime.min.time())) + pd.Timedelta(hours=h0, minutes=m0)
            t1 = t0 + pd.Timedelta(minutes=dur_m)
            t0 = max(t0, day_start)
            t1 = min(t1, day_end)
            if t1 <= t0:
                t1 = t0 + pd.Timedelta(minutes=28)
            rows_out.append(
                {
                    "Task": task,
                    "Start": t0,
                    "Finish": t1,
                    "State": state,
                    "내순위": rank,
                    "Top1부동산": top1,
                }
            )
    complex_data["timeline"] = pd.DataFrame(rows_out)


def _patch_unauth_demo_complex_data(complex_data: dict[str, object], chart_day: datetime.date) -> None:
    """체험용: comp 패턴·경쟁사 카드 문구·매물별 광고 추천 시각을 차트 트래픽 구간과 맞춘다."""
    dinner_band = "19:30~21:30"
    comp = complex_data.get("comp")
    if isinstance(comp, pd.DataFrame) and not comp.empty and "부동산명" in comp.columns:
        comp = comp.copy()
        # clean_realtor_name 기준 키 — 감시 카드 패턴·빈도
        by_clean: dict[str, dict[str, object]] = {
            "베스트중개": {
                "주력 갱신 시간": "10~12시",
                "오늘 요일 주력 시간": "10~12시",
                "오늘 요일 마지노선": 11,
                "오늘_요일_그룹": "화~목",
                "오늘요일_실측": True,
                "갱신빈도": "🔥 매일 갱신",
            },
            "행복공인": {
                "주력 갱신 시간": "15~17시",
                "오늘 요일 주력 시간": "15~17시",
                "오늘 요일 마지노선": 16,
                "오늘_요일_그룹": "화~목",
                "오늘요일_실측": True,
                "갱신빈도": "👑 상위권 패턴",
            },
            "탑랭크중개": {
                "주력 갱신 시간": "09시 전후",
                "오늘 요일 주력 시간": "09시 전후",
                "오늘 요일 마지노선": 10,
                "오늘_요일_그룹": "화~목",
                "오늘요일_실측": True,
                "갱신빈도": "⚡ 게릴라 갱신",
            },
            "미래스토리": {
                "주력 갱신 시간": "13~15시",
                "오늘 요일 주력 시간": "13~15시",
                "오늘 요일 마지노선": 15,
                "오늘_요일_그룹": "화~목",
                "오늘요일_실측": True,
                "갱신빈도": "🔥 매일 갱신",
            },
        }
        for i in comp.index:
            raw_nm = str(comp.at[i, "부동산명"]).strip()
            ck = clean_realtor_name(raw_nm)
            if ck not in by_clean:
                continue
            for col, val in by_clean[ck].items():
                if col not in comp.columns:
                    comp[col] = pd.NA
                comp.at[i, col] = val
        complex_data["comp"] = comp

    act = complex_data.get("action")
    if isinstance(act, pd.DataFrame) and not act.empty and "Task" in act.columns:
        act = act.copy().reset_index(drop=True)
        danji = "행복동 사랑단지"
        specs = _unauth_demo_listing_specs()
        task_meta: dict[str, dict[str, str | int]] = {}
        for idx, sp in enumerate(specs):
            tk = _task_label_from_spec(danji, str(sp["dong"]), str(sp["floor"]), sp["price"])
            eve_hm = str(sp["eve2"])

            # [수정] 1순위 추천 시각을 매물마다 완전히 다르게 분산 (빨간 별표 일직선 해결)
            rng = np.random.RandomState(idx + 42)
            h = int(rng.randint(10, 16))
            m = int(rng.randint(0, 59))

            advice = (
                f"💡 1순위: {h:02d}:{m:02d} (AI 최적 타점) / "
                f"💡 2순위: {eve_hm} (저녁 피크 · {dinner_band})"
            )
            task_meta[tk] = {
                "advice": advice,
                "hold": str(sp["hold"]),
                "renew": int(sp["renew"]),
                "last_up": str(sp["last_up"]),
            }

        strat: dict[str, str] = {}
        for i in range(len(act)):
            tk = str(act.at[i, "Task"]).strip()
            meta = task_meta.get(tk)
            if meta is None:
                continue
            act.at[i, "광고 추천 시간"] = meta["advice"]
            act.at[i, "상위권 유지 기간"] = meta["hold"]
            act.at[i, "광고 갱신 횟수"] = meta["renew"]
            act.at[i, "최근 갱신 시각"] = meta["last_up"]
            strat[tk] = str(meta["advice"])
        complex_data["action"] = act
        complex_data["strategy_dict"] = strat

    _patch_unauth_demo_timeline_realistic(complex_data, chart_day)


def _unauth_demo_watch_card_rows() -> list[dict[str, str | bool]]:
    """미인증 감시망 카드 4건 — `target_status` 엔트리를 동일 렌더러로 덮어쓸 때 사용."""
    _g = "color:#64748b;font-size:0.9rem;"
    _s = "color:#94a3b8;font-size:0.8rem;"
    return [
        {
            "clean_key": "베스트중개",
            "display_short": "베스트중개",
            "freq": "",
            "icon": "🔥",
            "type": "고빈도 추격조",
            "html": (
                "<b>🟢 오늘 광고 완료 (09:15 진행)</b><br>"
                f"<span style='{_g}'>평소: 09~11시 집중</span><br>"
                f"<span style='{_s}'>마지노선: 12:00</span>"
            ),
            "is_waiting": False,
            "is_done_today": True,
            "last_active_time": "09:15",
        },
        {
            "clean_key": "행복공인",
            "display_short": "행복공인",
            "freq": "",
            "icon": "👑",
            "type": "상위권 방어조",
            "html": (
                "<b>🔴 아직 활동 전 (주의)</b><br>"
                f"<span style='{_g}'>평소: 15~17시 집중</span><br>"
                f"<span style='{_s}'>마지노선: 16:30 (이후 안전)</span>"
            ),
            "is_waiting": True,
            "is_done_today": False,
            "last_active_time": "어제 16:10",
        },
        {
            "clean_key": "탑랭크중개",
            "display_short": "탑랭크중개",
            "freq": "",
            "icon": "⚡",
            "type": "게릴라 갱신조",
            "html": (
                "<b>🔵 활동 없음 (마지노선 경과)</b><br>"
                f"<span style='{_g}'>평소: 오전 10시 몰빵형</span><br>"
                f"<span style='{_s}'>마지노선 11시 경과</span>"
            ),
            "is_waiting": False,
            "is_done_today": False,
            "last_active_time": "어제 10:15",
        },
        {
            "clean_key": "미래스토리",
            "display_short": "미래부동산",
            "freq": "",
            "icon": "🔥",
            "type": "고빈도 추격조",
            "html": (
                "<b>🟢 오늘 광고 완료 (14:50 진행)</b><br>"
                f"<span style='{_g}'>평소: 13~15시 집중</span><br>"
                f"<span style='{_s}'>마지노선: 15:30</span>"
            ),
            "is_waiting": False,
            "is_done_today": True,
            "last_active_time": "14:50",
        },
    ]


def _apply_unauth_demo_watch_target_overrides(target_status: dict) -> None:
    """미인증: 감시 경쟁사 카드 표시용 필드를 데모 시나리오로 덮어쓴다(렌더 함수는 그대로)."""
    for row in _unauth_demo_watch_card_rows():
        ck = str(row["clean_key"])
        if ck not in target_status:
            continue
        info = target_status[ck]
        info["display_short"] = html.escape(str(row["display_short"]))
        info["freq"] = str(row["freq"])
        info["icon"] = str(row["icon"])
        info["type"] = str(row["type"])
        info["html"] = str(row["html"])
        info["is_waiting"] = bool(row["is_waiting"])
        info["is_done_today"] = bool(row["is_done_today"])
        info["last_active_time"] = str(row["last_active_time"])


def main() -> None:
    _logo_path = os.path.join(_APP_DIR, "LOGO.png")
    st.set_page_config(
        page_title="TOP RANK | 타임라인 방어 보드 (v2)",
        page_icon=_logo_path if os.path.isfile(_logo_path) else "📅",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if "has_logged_visit" not in st.session_state:
        st.session_state.has_logged_visit = True
        log_user_action("1. 대시보드 최초 접속")

    st.markdown(
        """
<style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
    html, body, [class*="css"], .stMarkdown, .stText {
        font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, sans-serif !important;
    }
    .stApp { background-color: #F8FAFC; }
    header, #MainMenu, footer {visibility: hidden;}
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 2.5rem !important;
        max-width: 1400px !important;
    }
    /* 탭(Tabs) 스타일링 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px; background-color: #F1F5F9; padding: 8px 12px; border-radius: 12px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 48px; background-color: transparent; border-radius: 8px;
        color: #64748B; font-weight: 600; font-size: 1.05rem; padding: 0 20px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #FFFFFF !important; color: #0F172A !important; box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
</style>
""",
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)

    user_id = st.query_params.get("id")
    IS_UNAUTH_DEMO = not user_id
    if IS_UNAUTH_DEMO:
        st.info("💡 체험단 문의는 010-8416-2806 으로 연락주시면 친절히 안내해드리겠습니다.")

    if IS_UNAUTH_DEMO:
        filter_realtor_name = "사랑공인중개사사무소"
        display_realtor = filter_realtor_name
        demo_name = filter_realtor_name
        IS_DEMO_MODE = False
        target_complexes = ["행복동 사랑단지"]
        raw_df = _build_unauth_demo_raw_df()
    else:
        REALTOR_MAP = load_realtor_map()
        if user_id not in REALTOR_MAP:
            user_id = "demo"
        IS_DEMO_MODE = user_id == "demo"
        current_realtor = REALTOR_MAP.get(user_id)
        if isinstance(current_realtor, dict):
            filter_realtor_name = current_realtor.get("name", "체험용 부동산")
            target_complexes = current_realtor.get("complexes", [])
        else:
            filter_realtor_name = str(current_realtor)
            target_complexes = []

        raw_demo = REALTOR_MAP.get("demo", {"name": "체험용 부동산"})
        demo_name = raw_demo.get("name", "체험용 부동산") if isinstance(raw_demo, dict) else str(raw_demo)
        display_realtor = demo_name if IS_DEMO_MODE else filter_realtor_name

        raw_df = load_server_data()
        if raw_df is not None and target_complexes:
            raw_df = raw_df[raw_df["단지명"].isin(target_complexes)].copy()

    if "guide_messages" not in st.session_state:
        st.session_state.guide_messages = [
            {
                "role": "assistant",
                "content": (
                    "대표님, 탑랭크 AI 비서입니다. 대시보드의 원리가 궁금하시다면 아래 버튼을 눌러주세요."
                ),
            },
            {"role": "assistant", "content": _CUSTOMER_WHITEPAPER_MD},
        ]

    if raw_df is None:
        st.error(f"데이터 파일을 찾지 못했습니다. 경로: `{DATA_DIR}`")
        st.stop()

    df = process_data(raw_df)
    if "CP사" in df.columns:
        df = df[
            ~df["CP사"].fillna("").astype(str).str.contains("한국공인중개사협회", na=False)
        ].copy()
    df = _prepare_listing_identity(df)
    if "수집일시" in df.columns:
        df["수집일시"] = pd.to_datetime(df["수집일시"], errors="coerce")

    min_time, max_time = df["수집일시"].min(), df["수집일시"].max()
    if df.empty or pd.isna(min_time) or pd.isna(max_time):
        st.error("일간 마감 컷오프 이후 분석 가능한 데이터가 없습니다.")
        st.stop()

    st.sidebar.header("분석 기간")
    KST = timezone(timedelta(hours=9))
    today_kst = datetime.now(KST).date()
    default_start_date = max(min_time.date(), max_time.date() - timedelta(days=14))
    s_d = st.sidebar.date_input("시작일", default_start_date, key="tl_sd")
    e_d = st.sidebar.date_input("종료일", today_kst, key="tl_ed")

    start_dt = pd.to_datetime(s_d)
    end_dt = pd.to_datetime(e_d) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    # [최적화] 전체 52만 건을 매번 검색하지 않도록 날짜 마스크로 먼저 줄임
    mask = (df["수집일시"] >= start_dt) & (df["수집일시"] <= end_dt)
    if target_complexes:
        mask = mask & df["단지명"].isin(target_complexes)

    filtered_df = df.loc[mask]
    if filtered_df.empty:
        st.error("선택한 기간에 데이터가 없습니다.")
        st.stop()

    _complex_choices = sorted(filtered_df["단지명"].dropna().unique().tolist())
    if not _complex_choices:
        st.error("선택한 기간에 단지명이 있는 데이터가 없습니다.")
        st.stop()

    if IS_UNAUTH_DEMO:
        master_data_dict = _precompute_all_complexes_data_impl(
            filtered_df, _complex_choices, filter_realtor_name, e_d
        )
    else:
        master_data_dict = precompute_all_complexes_data(
            filtered_df, _complex_choices, filter_realtor_name, e_d
        )

    _sel_complex = st.sidebar.selectbox(
        "단지명",
        options=_complex_choices,
        index=0,
        key="tl_complex_filter",
    )

    if "last_viewed_complex" not in st.session_state:
        st.session_state.last_viewed_complex = _sel_complex
    elif st.session_state.last_viewed_complex != _sel_complex:
        log_user_action(f"2. 단지 조회 변경: {_sel_complex}")
        st.session_state.last_viewed_complex = _sel_complex

    complex_data = master_data_dict.get(_sel_complex)
    if complex_data is None:
        st.error("해당 단지의 계산된 데이터가 없습니다.")
        st.stop()

    if IS_UNAUTH_DEMO:
        _patch_unauth_demo_complex_data(complex_data, e_d)

    action_df = complex_data["action"]
    if "tl_task_sort" not in st.session_state:
        st.session_state.tl_task_sort = _TASK_SORT_OPTIONS[0]
    timeline_df = complex_data["timeline"]
    ms_df = complex_data.get("ms", pd.DataFrame())
    comp_df = complex_data.get("comp", pd.DataFrame())
    if IS_UNAUTH_DEMO:
        ms_df = _unauth_ms_top10_df()

    # B2B SaaS 레이아웃: KPI 4열 + Tabs (트렌드 / 타임라인 / M·S)
    if True:
        tl_plot, day_start, day_end = _clip_timeline_to_chart_day(timeline_df, e_d)
        if tl_plot.empty:
            action_df_48h = _empty_action_df()
        else:
            active_tasks = tl_plot["Task"].dropna().unique()
            action_df_48h = action_df[action_df["Task"].isin(active_tasks)].copy()

        st.markdown(
            f'<div style="margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid #E2E8F0;"><span style="font-size: 2.2rem; font-weight: 800; color: #0F172A;">🏢 {filter_realtor_name} 전용 리포트</span><br><span style="font-size: 1.05rem; color: #64748B; font-weight: 500;">종료일({e_d.month}/{e_d.day}) 기준 최근 48시간 내 활동 이력이 있는 핵심 매물 분석 결과입니다.</span></div>',
            unsafe_allow_html=True,
        )
        with st.expander("📘 고객용 가이드 (백서)", expanded=False):
            st.markdown(_CUSTOMER_WHITEPAPER_MD)

        eff_total = _timeline_efficiency_score_from_tl_plot(tl_plot)
        if IS_UNAUTH_DEMO:
            eff_total = 63.0
        eff_day_prev = e_d - timedelta(days=1)
        eff_color = "#10B981" if eff_total >= 80 else ("#F59E0B" if eff_total >= 50 else "#EF4444")

        n_total = len(action_df_48h) if not action_df_48h.empty else 0
        n_ok = int((action_df_48h["상태"] == "✅ 방어 중").sum()) if not action_df_48h.empty else 0
        n_bad = int((action_df_48h["상태"] == "❌ 효력 종료").sum()) if not action_df_48h.empty else 0

        st.markdown(
            f"""
        <div style="display: flex; gap: 16px; margin-top: 10px; margin-bottom: 24px; flex-wrap: wrap;">
            <div style="flex: 1; min-width: 220px; background-color: #185294; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); color: #FFFFFF;">
                <div style="font-size: 14px; font-weight: 500; opacity: 0.9; margin-bottom: 8px;">🎯 AI 광고 효율 총점</div>
                <div style="display: flex; align-items: baseline; gap: 4px;">
                    <span style="font-size: 34px; font-weight: 700; line-height: 1;">{eff_total:.1f}</span>
                    <span style="font-size: 16px; font-weight: 500; opacity: 0.8;">점</span>
                </div>
            </div>
            <div style="flex: 1; min-width: 220px; background-color: #0986B6; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); color: #FFFFFF;">
                <div style="font-size: 14px; font-weight: 500; opacity: 0.9; margin-bottom: 8px;">전체 감시 매물</div>
                <div style="font-size: 34px; font-weight: 700; line-height: 1;">{n_total:,}</div>
            </div>
            <div style="flex: 1; min-width: 220px; background-color: #25B196; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); color: #FFFFFF;">
                <div style="font-size: 14px; font-weight: 500; opacity: 0.9; margin-bottom: 8px;">🟢 현재 상위권 (1~3위)</div>
                <div style="font-size: 34px; font-weight: 700; line-height: 1;">{n_ok:,}</div>
            </div>
            <div style="flex: 1; min-width: 220px; background-color: #745396; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); color: #FFFFFF;">
                <div style="font-size: 14px; font-weight: 500; opacity: 0.9; margin-bottom: 8px;">🔴 상위권 이탈</div>
                <div style="font-size: 34px; font-weight: 700; line-height: 1;">{n_bad:,}</div>
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        tab_command, tab_timeline, tab_trend, tab_ms = st.tabs(
            [
                "📡 광고 전술판",
                "📊 점유율 타임라인",
                "📈 일간 점수 트렌드",
                "🏆 단지 내 시장 점유율 (M/S)",
            ]
        )


        with tab_command:
            _cmd_sort_mode = _render_tracking_tab_header(
                "<h3 style='color:#0F172A;margin:0 0 4px 0;'>📡 광고 전술판</h3>",
                widget_key="tl_task_sort_command",
            )
            if tl_plot.empty:
                st.info(f"**{e_d}** 일자에 표시할 활동 매물이 없습니다.")
            else:
                kst_now_cmd = _now_kst_naive()
                kst_today_cmd = kst_now_cmd.date()
                if IS_UNAUTH_DEMO:
                    kst_now_cmd = pd.Timestamp.combine(kst_today_cmd, datetime.min.time()) + timedelta(
                        hours=10, minutes=19
                    )
                    kst_today_cmd = kst_now_cmd.date()

                t_df_cmd = filtered_df[filtered_df["단지명"] == _sel_complex].copy()
                if not t_df_cmd.empty and "Task" not in t_df_cmd.columns:
                    t_df_cmd["Task"] = t_df_cmd.apply(
                        lambda r: _task_label_from_spec(
                            r.get("단지명", ""),
                            r.get("동/호수", ""),
                            r.get("층/타입", ""),
                            _scalar_price_str(r.get("가격", "")),
                            r.get("방향", ""),
                        ),
                        axis=1,
                    )
                my_unified_cmd = clean_realtor_name(filter_realtor_name)
                if not t_df_cmd.empty:
                    t_df_cmd["부동산명_통합"] = t_df_cmd["부동산명"].apply(clean_realtor_name)

                _raw_cmd_tasks = tl_plot["Task"].dropna().unique().tolist()
                active_tasks_cmd = [
                    t
                    for t in _raw_cmd_tasks
                    if _renewal_events_28d_for_task(str(t), t_df_cmd, kst_today_cmd) >= 2
                ]

                st.caption(
                    f"기준 시각 **{kst_now_cmd.strftime('%H:%M')}** · 감시 매물 **{len(active_tasks_cmd)}**건"
                )

                if not active_tasks_cmd:
                    st.info(
                        "최근 28일 내 갱신 이력이 2건 미만인 매물(유령 부동산)은 지휘 통제 목록에서 제외됩니다."
                    )
                else:
                    strategy_dict_cmd = complex_data.get("strategy_dict", {})
                    _command_rows: list[tuple] = []
                    for _task_cmd in active_tasks_cmd:
                        _ts_cmd = _build_target_status_for_task(
                            _task_cmd,
                            t_df_cmd,
                            complex_data,
                            comp_df,
                            kst_now=kst_now_cmd,
                            kst_today=kst_today_cmd,
                            my_unified=my_unified_cmd,
                            is_demo_mode=IS_DEMO_MODE,
                            is_unauth_demo=IS_UNAUTH_DEMO,
                            filter_realtor_name=filter_realtor_name,
                            display_realtor=display_realtor,
                        )
                        _ai_cmd = strategy_dict_cmd.get(_task_cmd, "") or ""
                        _any_wait_cmd = (
                            any(v.get("is_waiting") for v in _ts_cmd.values()) if _ts_cmd else False
                        )
                        _act_cmd = _determine_action_state(
                            target_status=_ts_cmd,
                            any_waiting=_any_wait_cmd,
                            ai_msg=_ai_cmd,
                            kst_now=kst_now_cmd,
                        )
                        _command_rows.append((_task_cmd, _act_cmd, _ai_cmd, _ts_cmd))

                    _sorted_cmd_tasks = _sort_tracking_tasks(
                        active_tasks_cmd, action_df, _cmd_sort_mode
                    )
                    _cmd_by_task = {row[0]: row for row in _command_rows}

                    for _idx_cmd, _task_cmd in enumerate(_sorted_cmd_tasks):
                        _row_cmd = _cmd_by_task.get(_task_cmd)
                        if not _row_cmd:
                            continue
                        _task_cmd, _act_cmd, _ai_cmd, _ts_cmd = _row_cmd
                        _status_label, _status_accent = _status_badge_from_action(_act_cmd)
                        _ai_summary = _format_ai_recommendation_summary(_ai_cmd)
                        _render_command_summary_row(
                            _task_cmd,
                            _ts_cmd,
                            status_label=_status_label,
                            status_accent=_status_accent,
                            ai_summary=_ai_summary,
                            expander_key=f"cmd_detail_{_idx_cmd}",
                        )

        with tab_trend:
            # ==========================================
            # [UI 개선] 점수 카드와 트렌드 카드 높이 완벽 정렬
            # ==========================================
            _score_trend_chart_h = 240  # 차트와 박스 높이를 맞추기 위해 강제 고정

            st.caption("📈 **일간 점수 트렌드 (최근 2주)**")

            spark_start = max(e_d - timedelta(days=13), start_dt.date())
            dates = pd.date_range(start=spark_start, end=end_dt.date())

            if IS_UNAUTH_DEMO:
                _t0 = max(e_d - timedelta(days=13), start_dt.date())
                spark_days = [_t0 + timedelta(days=i) for i in range(14)]
                # [수정] 밋밋하지 않게 극적인 우상향 그래프로 데이터 변경
                _scores_14 = [38, 42, 35, 52, 48, 65, 55, 72, 68, 85, 78, 92, 88, 95]
                df_trend = pd.DataFrame({"날짜": spark_days, "점수": _scores_14})
            else:
                trend_data = []
                for d in dates:
                    d_date = d.date()
                    t_tl, _, _ = _clip_timeline_to_chart_day(timeline_df, d_date)
                    sc = _timeline_efficiency_score_from_tl_plot(t_tl)
                    trend_data.append({"날짜": d_date, "점수": sc})
                df_trend = pd.DataFrame(trend_data)
                if df_trend.empty:
                    df_trend = pd.DataFrame([{"날짜": e_d, "점수": float(eff_total)}])

            # 차트 렌더링
            fig_spark = px.line(df_trend, x="날짜", y="점수", markers=True)
            fig_spark.update_traces(
                line_color="#3B82F6",
                line_width=3,
                marker=dict(size=8, color="white", line=dict(color="#1E40AF", width=2)),
            )

            # [수정] Y축 라벨 25단위 고정 (데모, 실제 모두 완벽 적용)
            _spark_yaxis = dict(
                title="",
                visible=True,
                showgrid=True,
                gridcolor="#F1F5F9",
                range=[0, 105],
                fixedrange=True,
                tickmode="array",
                tickvals=[0, 25, 50, 75, 100],
                ticktext=["0", "25", "50", "75", "100"],
            )

            fig_spark.update_layout(
                margin=dict(l=10, r=10, t=20, b=10),
                xaxis=dict(
                    title="",
                    visible=True,
                    showgrid=False,
                    fixedrange=True,
                    tickformat="%m/%d",
                ),
                yaxis=_spark_yaxis,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                height=_score_trend_chart_h,  # 좌측 박스와 완벽하게 일치
                hovermode="x unified",
            )
            st.plotly_chart(fig_spark, use_container_width=True, config={"displayModeBar": False})

        with tab_timeline:
            _tl_sort_mode = _render_tracking_tab_header(
                "<h3 style='color:#0F172A;margin:0 0 4px 0;'>📊 점유율 타임라인</h3>",
                widget_key="tl_task_sort_timeline",
            )
            # Toss 스타일: 방어=블루, 순위 밀림=라이트 그레이(비움 느낌)
            _toss_title = "#191F28"
            _toss_body = "#333D4B"
            _toss_sub = "#8B95A1"
            _toss_blue = "#3182F6"
            _toss_bg_gray = "#F2F4F6"
            _toss_line_gray = "#E5E8EB"
            state_colors = {
                "🟢 1~3위 방어 중": _toss_blue,
                "🔴 경쟁사 진입 (순위 밀림)": _toss_line_gray,
            }

            if tl_plot.empty:
                st.info(f"**{e_d}** 일자에 타임라인으로 표시할 수집 구간이 없습니다. (분석 기간·데이터를 확인하세요.)")
            else:
                # 1. 완벽한 매물 매칭 함수 (단지명과 동/호수 분리 탐색으로 100% 매칭 보장)
                def _get_row(t_str):
                    for adf in (action_df, action_df_48h):
                        if adf is None or adf.empty:
                            continue
                        t_clean = str(t_str).replace(" ", "")
                        if "Task" in adf.columns:
                            for _, r in adf.iterrows():
                                if str(r.get("Task", "")).replace(" ", "") == t_clean:
                                    return r
                        parts = str(t_str).replace("(", "").replace(")", "").split()
                        if len(parts) >= 2:
                            for _, r in adf.iterrows():
                                m_name = str(r.get("매물명", ""))
                                if parts[0] in m_name and parts[1] in m_name:
                                    return r
                    return None

                # 2. 정렬 및 Y축 갱신 횟수 라벨 구축 (undefined·공백 등 쓰레기 라벨 제외 → 상단 공백 완화)
                raw_tasks = tl_plot["Task"].dropna().unique().tolist()
                unique_tasks = [
                    t
                    for t in raw_tasks
                    if str(t).strip()
                    and str(t).strip().lower() != "undefined"
                    and str(t).strip().lower() != "nan"
                ]
                sort_info = {}
                for t in unique_tasks:
                    r = _get_row(t)
                    if r is not None:
                        dj = str(r.get("단지명", ""))
                        cnt = int(pd.to_numeric(r.get("광고 갱신 횟수", 0), errors="coerce") or 0)
                        last_up = str(r.get("최근 갱신 시각", "") or "").strip() or "기록 없음"
                        sort_info[t] = (dj, cnt, last_up, r)
                    else:
                        sort_info[t] = ("", 0, "기록 없음", None)

                task_order = _sort_tracking_tasks(unique_tasks, action_df, _tl_sort_mode)
                ticktext_list = [
                    f"{t}  ·  🔄 {sort_info[t][1]}회  ·  🕒 {sort_info[t][2]}"
                    for t in task_order
                ]

                task_order_chart = task_order
                ticktext_chart = ticktext_list

                # ==========================================
                # 아래부터는 기존 px.timeline 그리는 코드 (display_tl_df 치환 없이 원본 tl_hover 사용)
                # 3. 차트 기본 렌더링 (툴팁: 내 순위·1위 부동산 마스킹, 호버 프레임은 캐시)
                tl_hover = _build_plotly_hover_frame(
                    tl_plot,
                    IS_DEMO_MODE,
                    filter_realtor_name,
                    display_realtor,
                )
                tl_hover = tl_hover[tl_hover["Task"].isin(task_order_chart)].copy()
                for _c in ("Task", "State", "_hv_s", "_hv_f", "_hv_st", "_hv_rank", "_hv_extra"):
                    if _c in tl_hover.columns:
                        tl_hover[_c] = tl_hover[_c].astype(str).str.replace("\ufffd", "", regex=False)

                fig = px.timeline(
                    tl_hover,
                    x_start="Start",
                    x_end="Finish",
                    y="Task",
                    color="State",
                    color_discrete_map=state_colors,
                    custom_data=["_hv_s", "_hv_f", "_hv_st", "_hv_rank", "_hv_extra"],
                )
                _plot_font = "'Pretendard', 'Noto Sans KR', sans-serif"
                _grid_soft = "rgba(229, 232, 235, 0.85)"
                _toss_red = "#F04452"
                fig.update_traces(
                    hovertemplate=(
                        "매물: %{y}<br>"
                        "시간: %{customdata[0]} ~ %{customdata[1]}<br>"
                        "상태: %{customdata[2]}<br>"
                        "내 순위: %{customdata[3]}<br>"
                        "%{customdata[4]}"
                        "<extra></extra>"
                    ),
                    width=0.25,
                    selector=dict(type="bar"),
                )

                # 4. Y축 및 X축 강제 스타일링 (Y축 줌 고정 없음 → Plotly 높이 + iframe 스크롤)
                fig.update_yaxes(
                    autorange="reversed",
                    automargin=True,
                    categoryorder="array",
                    categoryarray=task_order_chart,
                    tickmode="array",
                    tickvals=task_order_chart,
                    ticktext=ticktext_chart,
                    tickfont=dict(
                        family=_plot_font,
                        size=14,
                        color=_toss_body,
                    ),
                    showgrid=True,
                    gridcolor=_grid_soft,
                    gridwidth=1,
                    zeroline=False,
                )
                # X축: 2시간 간격(4·6·8…)으로 정돈, 시간만 표시
                _two_h_ms = 7200000
                _x_tick0 = pd.Timestamp(day_start)
                fig.update_xaxes(
                    side="top",
                    type="date",
                    range=[day_start, day_end],
                    tick0=_x_tick0,
                    dtick=_two_h_ms,
                    tickformat="%H:00",
                    tickformatstops=[
                        dict(dtickrange=[None, None], value="%H:00"),
                    ],
                    tickangle=0,
                    tickfont=dict(family=_plot_font, size=9, color=_toss_sub),
                    showgrid=True,
                    gridcolor=_grid_soft,
                    gridwidth=1,
                    title="",
                )
                fig.update_layout(
                    dragmode="pan",
                    font=dict(family=_plot_font, size=12, color=_toss_body),
                    title=dict(font=dict(family=_plot_font, size=14, color=_toss_title)),
                    paper_bgcolor="white",
                    plot_bgcolor="white",
                    height=max(500, len(task_order_chart) * 40),
                    margin=dict(l=300, r=20, t=56, b=88),
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.14,
                        xanchor="center",
                        x=0.5,
                        font=dict(family=_plot_font, size=11, color=_toss_body),
                        bgcolor="rgba(255,255,255,0.92)",
                        bordercolor=_toss_line_gray,
                        borderwidth=1,
                    ),
                )

                for _night_day in (e_d - timedelta(days=1), e_d):
                    t_n0 = pd.Timestamp(datetime.combine(_night_day, datetime.min.time()))
                    t_n1 = t_n0 + pd.Timedelta(hours=7, minutes=59, seconds=59)
                    fig.add_vrect(
                        x0=t_n0,
                        x1=t_n1,
                        fillcolor="rgba(148, 163, 184, 0.15)",
                        layer="below",
                        line_width=0,
                        xref="x",
                        yref="paper",
                        y0=0,
                        y1=1,
                    )

                _peak_slots = ((11, 30, 13, 30), (19, 30, 21, 30))
                for _pd in (e_d - timedelta(days=1), e_d):
                    _day0 = pd.Timestamp(datetime.combine(_pd, datetime.min.time()))
                    for _slot_i, (_h0, _m0, _h1, _m1) in enumerate(_peak_slots):
                        _pk0 = _day0 + pd.Timedelta(hours=_h0, minutes=_m0)
                        _pk1 = _day0 + pd.Timedelta(hours=_h1, minutes=_m1)
                        fig.add_vrect(
                            x0=_pk0,
                            x1=_pk1,
                            fillcolor="rgba(250, 204, 21, 0.15)",
                            layer="below",
                            line_width=0,
                            xref="x",
                            yref="paper",
                            y0=0,
                            y1=1,
                        )
                        _peak_lbl = "트래픽 집중"
                        if IS_UNAUTH_DEMO:
                            _peak_lbl = "점심·11:30~13:30" if _slot_i == 0 else "저녁·19:30~21:30"
                        fig.add_annotation(
                            x=_pk0 + (_pk1 - _pk0) / 2,
                            xref="x",
                            y=0.98,
                            yref="paper",
                            text=_peak_lbl,
                            showarrow=False,
                            font=dict(family=_plot_font, size=9, color="#CA8A04"),
                            yanchor="top",
                        )

                # 5. 자정(00:00) 오늘 시작 선 긋기
                midnight_ts = pd.Timestamp(datetime.combine(e_d, datetime.min.time()))
                fig.add_vline(
                    x=midnight_ts,
                    line_width=1.25,
                    line_dash="solid",
                    line_color="rgba(139, 149, 161, 0.35)",
                )
                fig.add_annotation(
                    x=midnight_ts,
                    y=1.02,
                    xref="x",
                    yref="paper",
                    text=f"📅 {e_d.month}/{e_d.day} 시작",
                    showarrow=False,
                    font=dict(family=_plot_font, size=10, color=_toss_sub),
                    xanchor="left",
                    bgcolor="rgba(242, 244, 246, 0.95)",
                    bordercolor=_toss_line_gray,
                    borderwidth=1,
                    borderpad=5,
                )

                # 6. 현재 시점 선 긋기
                ref_ts = _reference_guide_timestamp(action_df, e_d, day_start, day_end)
                fig.add_shape(
                    type="line",
                    x0=ref_ts,
                    x1=ref_ts,
                    y0=0,
                    y1=1,
                    xref="x",
                    yref="paper",
                    line=dict(color="rgba(49, 130, 246, 0.55)", width=1.25, dash="dot"),
                )
                fig.add_annotation(
                    x=ref_ts,
                    y=1.02,
                    xref="x",
                    yref="paper",
                    text="현재",
                    showarrow=False,
                    font=dict(family=_plot_font, size=10, color=_toss_title),
                    xanchor="right",
                    bgcolor="rgba(49, 130, 246, 0.08)",
                    bordercolor="rgba(49, 130, 246, 0.25)",
                    borderwidth=1,
                    borderpad=5,
                )

                # [혁신적 우회법] Streamlit의 80KB 청크 절단 버그를 원천 봉쇄                # [혁신적 우회법] Streamlit의 80KB 청크 절단 버그를 원천 봉쇄
                # scrollZoom=False: 휠이 차트 확대가 아니라 페이지 세로 스크롤에 가깝게 동작
                html_str = fig.to_html(
                    include_plotlyjs="cdn",
                    full_html=True,
                    config={"displayModeBar": True, "scrollZoom": False, "displaylogo": False},
                )
                b64_html = base64.b64encode(html_str.encode("utf-8")).decode("utf-8")
                _chart_h = 750
                st.markdown(
                    f'<iframe src="data:text/html;base64,{b64_html}" '
                    f'width="100%" height="{_chart_h}" '
                    f'style="border:none; overflow:hidden;"></iframe>',
                    unsafe_allow_html=True,
                )
        with tab_ms:
            st.markdown("#### 🏆 단지 내 시장 점유율 (M/S) Top 10")
            st.caption("파워점수 공식 = 기본(10) + 순위가점(10/순위) + 물량가점(묶음개수*0.1)")

            c_m1, c_m2 = st.columns([1, 1.2])
            if not ms_df.empty:
                ms_df = ms_df.copy()
                ms_df["부동산명_축약"] = ms_df["부동산명"].apply(
                    lambda x: mask_text(
                        clean_realtor_name(x),
                        is_demo=IS_DEMO_MODE,
                        filter_realtor_name=filter_realtor_name,
                        display_realtor=display_realtor,
                    )
                )
                top10_ms = ms_df.sort_values("총점수", ascending=False).head(10)

                with c_m1:
                    st.dataframe(
                        top10_ms[["부동산명_축약", "매물건수", "총점수"]],
                        use_container_width=True,
                        hide_index=True,
                    )
                with c_m2:
                    top10_ms_chart = top10_ms.sort_values("총점수", ascending=True)
                    cleaned_my_realtor = clean_realtor_name(display_realtor)
                    top10_ms_chart = top10_ms_chart.copy()
                    top10_ms_chart["색상"] = top10_ms_chart["부동산명_축약"].apply(
                        lambda x: "#3B82F6" if x == cleaned_my_realtor else "#E2E8F0"
                    )

                    fig_ms = px.bar(
                        top10_ms_chart,
                        x="총점수",
                        y="부동산명_축약",
                        orientation="h",
                        text="총점수",
                    )
                    fig_ms.update_traces(
                        marker_color=top10_ms_chart["색상"], textposition="outside"
                    )
                    fig_ms.update_layout(
                        height=350,
                        margin=dict(t=0, b=0, l=0, r=0),
                        xaxis_visible=False,
                        yaxis_title="",
                        plot_bgcolor="rgba(0,0,0,0)",
                    )
                    st.plotly_chart(fig_ms, use_container_width=True, config={"displayModeBar": False})
            else:
                st.info("점유율 데이터가 없습니다.")

    st.sidebar.markdown("---")
    st.sidebar.subheader("🤖 탑랭크 AI 비서")

    _guide_scroll_html = (
        '<div style="max-height:min(42vh,360px);overflow-y:auto;overflow-x:hidden;padding:10px 8px;'
        'border:1px solid #E2E8F0;border-radius:10px;background:#FFFFFF;margin-bottom:10px;line-height:1.55;">'
        + "".join(
            '<div style="margin-bottom:12px;font-size:0.84rem;color:#334155;">'
            + _guide_md_fragments_to_html(msg["content"])
            + "</div>"
            for msg in st.session_state.guide_messages
        )
        + "</div>"
    )
    st.sidebar.markdown(_guide_scroll_html, unsafe_allow_html=True)

    gc1, gc2, gc3 = st.sidebar.columns(3)
    with gc1:
        if st.button("⏱️ 시간 추천 원리", key="guide_btn_time", use_container_width=True):
            st.session_state.guide_messages.append({"role": "assistant", "content": _GUIDE_REPLY_TIME})
            st.rerun()
    with gc2:
        if st.button("💯 점수 계산 방식", key="guide_btn_score", use_container_width=True):
            st.session_state.guide_messages.append({"role": "assistant", "content": _GUIDE_REPLY_SCORE})
            st.rerun()
    with gc3:
        if st.button("🌙 심야 시간 제외?", key="guide_btn_night", use_container_width=True):
            st.session_state.guide_messages.append({"role": "assistant", "content": _GUIDE_REPLY_NIGHT})
            st.rerun()

if __name__ == "__main__":
    main()
    st.caption("copyright 신성우")