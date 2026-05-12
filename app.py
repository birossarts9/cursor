"""
24시간·당일 기준 타임라인(Plotly 간트) 대시보드.
프라임 action_df / merge_asof 엔진은 app.py 마스터 대시보드와 동일합니다.
실행: streamlit run app_map_2.py
"""
from __future__ import annotations

import base64
import html
import os
import time

import re
from datetime import datetime, timedelta, timezone

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
# 엑셀 열 인덱스(0-based): N열 = CP사 — 멀티 CP 광고 핑퐁 노이즈 방지용 식별자
COL_CP = 13

_GUIDE_REPLY_TIME = (
    "최근 **28일(4주) 치의 타사 활동 데이터**를 분석합니다. 특히 최근 4일의 활동엔 가중치를 2배로 주어 최신 트렌드를 반영합니다. "
    "경쟁사가 갱신을 멈추는 **'빈집'** 구간을 찾고, 그 구간이 점심(11~13시)이나 저녁(19~21시) 같은 "
    "**피크 타임을 얼마나 길게 독점할 수 있는지 계산**하여 가장 효율이 높은 타격 시간을 추천합니다."
)
_GUIDE_REPLY_SCORE = (
    "현재 화면에 보이는 **48시간** 동안 대표님의 매물이 1~3위 상위권에 안전하게 떠 있던 시간의 비율입니다. "
    "화면의 **초록색 막대**가 길수록 점수가 100점에 가까워집니다."
)
_GUIDE_REPLY_NIGHT = (
    "네이버 부동산 방문객이 거의 없는 **00시부터 08시까지의 심야 시간**은 노출되어도 효과가 없기 때문에 "
    "점수 계산과 예상 노출 시간에서 **완전히 제외(0시간 처리)**합니다. 오직 진짜 영업시간에만 집중합니다."
)

_CUSTOMER_WHITEPAPER_MD = """
### 1. 타임라인 차트에는 어떤 매물이 뜨나요?
최근 48시간 이내에 상위권(1~3위)에 진입했거나, 밀려난 **'활동 이력'이 있는 매물만** 표시합니다. 48시간 내내 광고 갱신이 없었던 방치 매물은 차트에서 제외하여 핵심에만 집중합니다.

### 2. ⭐ 추천 시간(타격 시각)은 어떻게 정해지나요?
최근 **28일(4주) 치**의 타사 갱신 패턴을 정밀 분석합니다. 경쟁사가 갱신을 멈추는 **'빈집'** 구간을 찾고, 그 구간이 네이버 부동산 방문객이 가장 많은 **피크 타임(점심/저녁)을 얼마나 길게 독점할 수 있는지** 계산하여 가장 효율이 높은 타격 시간을 추천합니다.

### 3. 🌙 심야 시간은 왜 제외되나요?
방문객이 거의 없는 **00시부터 08시까지의 심야 시간**은 노출되어도 효과가 떨어집니다. 따라서 AI 점수 계산과 예상 노출 시간 합산에서 심야 시간은 **완전히 제외(0초 처리)**하여, 오직 진짜 영업시간의 효율만 측정합니다.
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


def _task_label_from_spec(
    danji: str, dongho: str, floor_type: str, price=None
) -> str:
    """타임라인·액션표 공통: `동/호수 (면적·층수 | 가격)` (단지명은 동/호수 중복 시에만 제거)."""
    dong_c = _strip_danji_from_dongho(danji, dongho)
    if not dong_c:
        dong_c = "—"
    mid = _area_floor_compact_label(floor_type)
    if not mid:
        mid = "—"
    ptxt = _fmt_price_kr(price)
    if not ptxt:
        ptxt = "—"
    return f"{dong_c} ({mid} | {ptxt})"


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

    # (A) AI 추천 시각에 도달했거나 이미 지났음 → 즉시 타격
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
        palette = {
            "bg": "linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%)",
            "border": "#10b981",
            "accent": "#047857",
            "text": "#065f46",
        }
        summary_text = "현재 화면에서 집계된 경쟁 감시 대상이 없습니다. 원하시는 시각에 자유롭게 갱신하셔도 됩니다."
    elif waiting_watch > 0:
        title = "🛑 대기 권장 (적군 활동 중)"
        palette = {
            "bg": "linear-gradient(135deg, #fff1f2 0%, #ffe4e6 100%)",
            "border": "#ef4444",
            "accent": "#b91c1c",
            "text": "#9f1239",
        }
        summary_text = (
            f"현재 감시 중인 총 {total_watch}곳 중 {waiting_watch}곳이 아직 갱신 대기 중입니다."
        )
    else:
        title = "🚀 즉시 타격 (경쟁사 활동 종료)"
        palette = {
            "bg": "linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%)",
            "border": "#2563eb",
            "accent": "#1d4ed8",
            "text": "#1e3a8a",
        }
        summary_text = (
            f"감시 중인 {total_watch}곳이 모두 오늘 갱신을 마쳤습니다. 지금이 가장 안전한 타점입니다."
        )

    raw_ai = (ai_msg or "").strip()
    ai_html = html.escape(raw_ai) if raw_ai else "AI 추천 문구를 확인 중입니다."
    ai_block = (
        "<div style='margin-top:14px; font-size:0.84rem; color:#94a3b8; line-height:1.5;'>"
        "<span style='font-weight:700;'>[과거 데이터 기반 AI 참고 타점]</span> "
        f"<span style='font-weight:500;'>{ai_html}</span>"
        "</div>"
    )

    st.markdown(
        f"""
<div style="
  background: {palette['bg']};
  border: 2px solid {palette['border']};
  border-left: 10px solid {palette['border']};
  border-radius: 14px;
  padding: 22px 26px;
  margin: 18px 0 16px 0;
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
  font-family: inherit;
">
  <div style="font-size: 1.55rem; font-weight: 900; color: {palette['accent']}; letter-spacing: -0.02em; line-height: 1.25;">
    {title}
  </div>
  <div style="font-size: 1.08rem; color: {palette['text']}; margin-top: 12px; line-height: 1.52; font-weight: 700;">
    {html.escape(summary_text)}
  </div>
  {ai_block}
</div>
""",
        unsafe_allow_html=True,
    )


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
            # 매물명 텍스트 내에 Task 문자열이 '완벽히' 포함될 때만 매칭
            if t_nospace in mn:
                return row

    return None


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
            }
        )
    spec_df = pd.DataFrame(rows_spec)
    tl = tl_src.merge(spec_df, on="매물묶음키", how="left")

    now_kst = _now_kst_naive()
    tl["Start"] = pd.to_datetime(tl["수집일시"], errors="coerce")
    raw_next = pd.to_datetime(tl["다음수집일시"], errors="coerce")
    end_cap = now_kst
    if pd.notna(batch_end_ts):
        end_cap = max(batch_end_ts, now_kst)
    tl["Finish"] = raw_next.fillna(end_cap)
    tl["Finish"] = pd.to_datetime(tl["Finish"], errors="coerce")
    bad = tl["Finish"].isna() | (tl["Finish"] <= tl["Start"])
    tl.loc[bad, "Finish"] = tl.loc[bad, "Start"] + pd.Timedelta(seconds=1)

    if "가격" not in tl.columns:
        tl["가격"] = ""
    else:
        tl["가격"] = tl["가격"].fillna("")

    tl["Task"] = [
        _task_label_from_spec(d, dh, ft, _scalar_price_str(pr))
        for d, dh, ft, pr in zip(
            tl["단지명"].tolist(),
            tl["동/호수"].tolist(),
            tl["층/타입"].tolist(),
            tl["가격"].tolist(),
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
    t_work = trk.copy()
    b_work = boosted_df.copy()
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
    latest_ts = b_work["수집일시"].max()
    if pd.isna(latest_ts):
        return _empty_action_df(), _empty_timeline_df()

    batch_end_ts = latest_ts.normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    evt_cols = key_cols + ["수집일시"]

    hist_cols = key_cols + ["수집일시", "묶음내순위_숫자", "전체순위_숫자", "노출형태"]
    hist_base = (
        t_work[hist_cols].dropna(subset=evt_cols).sort_values(key_cols + ["수집일시"]).copy()
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

    last_rows = hist_base_real.groupby(key_cols, dropna=False).tail(1)
    defense_mask = last_rows["is_top_tier"].fillna(False)
    if defense_mask.any():
        synthetic_rows = last_rows.loc[defense_mask].copy()
        synthetic_rows["수집일시"] = batch_end_ts
        synthetic_rows["is_top_tier"] = False
        hist_base = pd.concat([hist_base_real, synthetic_rows], ignore_index=True)
    else:
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
    for c in ["단지명", "동/호수", "층/타입", "거래방식", "가격"]:
        if c not in spec_src.columns:
            spec_src[c] = ""
        spec_src[c] = (
            spec_src[c]
            .astype(str)
            .str.strip()
            .replace({"nan": "", "None": "", "": pd.NA})
        )
    spec_grouped = spec_src.groupby("매물묶음키", dropna=False).last().fillna("")
    spec_map = spec_grouped[["단지명", "동/호수", "층/타입", "거래방식", "가격"]].to_dict(
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
                "Task": _task_label_from_spec(danji, dongho, floor_type, raw_price_src),
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


@st.cache_data(show_spinner="🚀 선택된 기간의 모든 단지 데이터를 미리 계산 중입니다... (최초 1회만 소요)")
def precompute_all_complexes_data(
    df_to_process: pd.DataFrame,
    complexes_list: list[str],
    realtor_name: str,
    target_date: datetime.date,
) -> dict[str, dict[str, pd.DataFrame]]:
    """기간·부동산 필터가 같을 때 단지 전환 시 재계산 없이 쓰기 위한 일괄 사전 계산."""
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
        grp_keys = ["부동산명_정제", "단지명", "동/호수", "층/타입", "거래방식", "가격", "CP사"]

        for c in grp_keys:
            if c in trk.columns:
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
                ]
            ).copy()

            uniq["묶음_총개수"] = uniq.groupby(
                ["단지명", "동/호수", "층/타입", "거래방식", "가격", "CP사"]
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


def main() -> None:
    _logo_path = os.path.join(_APP_DIR, "LOGO.png")
    st.set_page_config(
        page_title="TOP RANK | 타임라인 방어 보드 (v2)",
        page_icon=_logo_path if os.path.isfile(_logo_path) else "📅",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown(
        """
<link rel="stylesheet" crossorigin href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css" />
<style>
    html, body, .stApp,
    .block-container, .stMarkdown, [data-testid="stMarkdownContainer"],
    label, p, small, h1, h2, h3, h4, h5, h6, .stCaption {
        font-family: 'Pretendard', 'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }
    .stApp {
        background: #F8FAFC !important;
    }
    .block-container {
        padding-top: 3.5rem !important;
        padding-bottom: 2.5rem !important;
    }
    [data-testid="stMetric"] {
        background: #FFFFFF !important;
        border: 1px solid #E2E8F0 !important;
        border-radius: 12px !important;
        padding: 1rem 1.1rem !important;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.04) !important;
    }
    [data-testid="stMetric"] label {
        color: #64748B !important;
    }
    [data-testid="stVerticalBlockBorderWrapper"] {
        background: #FFFFFF !important;
        border: 1px solid #E2E8F0 !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.04) !important;
        padding: 0.35rem 0.5rem !important;
    }
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%) !important;
        border-right: 1px solid #E2E8F0 !important;
    }
    section[data-testid="stSidebar"] .stButton > button {
        border-radius: 9999px !important;
        border: 1px solid #E2E8F0 !important;
        background: #FFFFFF !important;
        color: #334155 !important;
        font-weight: 500 !important;
        padding: 0.45rem 0.65rem !important;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
        transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease !important;
    }
    section[data-testid="stSidebar"] .stButton > button:hover {
        background: #F1F5F9 !important;
        border-color: #CBD5E1 !important;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.06) !important;
    }
    section[data-testid="stSidebar"] .stButton > button:focus-visible {
        outline: 2px solid #94A3B8 !important;
        outline-offset: 2px !important;
    }
    /* 사이드바 필터 박스 시인성 강화 */
    section[data-testid="stSidebar"] .stDateInput > div > div > input,
    section[data-testid="stSidebar"] .stSelectbox > div > div > div {
        background-color: #EFF6FF !important;
        border: 1px solid #BFDBFE !important;
        font-weight: 600 !important;
        color: #1E3A8A !important;
        border-radius: 8px !important;
    }
    section[data-testid="stSidebar"] .stDateInput > div > div > input:focus,
    section[data-testid="stSidebar"] .stSelectbox > div > div > div:focus {
        border: 2px solid #3B82F6 !important;
        box-shadow: 0 0 0 1px #3B82F6 !important;
    }
    /* 탭(라디오 버튼 영역) 크기 뻥튀기 */
    button[data-baseweb="tab"] p {
        font-size: 1.25rem !important;
        font-weight: 800 !important;
    }
    /* 시작일/종료일 달력 입력창 파란색 배경 적용 */
    div[data-testid="stDateInput"] input {
        background-color: #EFF6FF !important;
        border: 1px solid #BFDBFE !important;
        color: #1E3A8A !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
    }
    div[data-testid="stDateInput"] input:focus {
        border: 2px solid #3B82F6 !important;
        box-shadow: 0 0 0 1px #3B82F6 !important;
    }
</style>
""",
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)

    query_params = st.query_params
    user_id = query_params.get("id", "a123")
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

    if "guide_messages" not in st.session_state:
        st.session_state.guide_messages = [
            {
                "role": "assistant",
                "content": (
                    "대표님, 탑랭크 AI 비서입니다. 대시보드의 원리가 궁금하시다면 아래 버튼을 눌러주세요."
                ),
            }
        ]

    raw_df = load_server_data()
    if raw_df is not None and target_complexes:
        raw_df = raw_df[raw_df["단지명"].isin(target_complexes)].copy()

    if raw_df is None:
        st.error(f"데이터 파일을 찾지 못했습니다. 경로: `{DATA_DIR}`")
        st.stop()

    df = process_data(raw_df)
    if "CP사" in df.columns:
        df = df[df["CP사"] != "한국공인중개사협회"].copy()
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

    master_data_dict = precompute_all_complexes_data(
        filtered_df, _complex_choices, filter_realtor_name, e_d
    )

    _sel_complex = st.sidebar.selectbox(
        "단지명",
        options=_complex_choices,
        index=0,
        key="tl_complex_filter",
    )

    complex_data = master_data_dict.get(_sel_complex)
    if complex_data is None:
        st.error("해당 단지의 계산된 데이터가 없습니다.")
        st.stop()

    action_df = complex_data["action"]
    timeline_df = complex_data["timeline"]
    ms_df = complex_data.get("ms", pd.DataFrame())
    comp_df = complex_data.get("comp", pd.DataFrame())

    # 탭을 제거하고 메인 화면 단일 레이아웃으로 통합
    if True:
        tl_plot, day_start, day_end = _clip_timeline_to_chart_day(timeline_df, e_d)
        if tl_plot.empty:
            action_df_48h = _empty_action_df()
        else:
            active_tasks = tl_plot["Task"].dropna().unique()
            action_df_48h = action_df[action_df["Task"].isin(active_tasks)].copy()

        # [UI 개선] 메인 리포트 타이틀 크기 확대
        st.markdown(
            f"""
            <div style="margin-bottom: 20px;">
                <span style="font-size: 2.0rem; font-weight: 800; color: #1E293B;">🏢 {filter_realtor_name} 전용 리포트</span>
                <br>
                <span style="font-size: 1.05rem; color: #64748B;">현재 표시된 차트 및 지표는 종료일({e_d.month}/{e_d.day}) 기준 최근 48시간 동안 활동 이력이 있는 핵심 매물만을 대상으로 분석되었습니다.</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander("📘 고객용 가이드 (백서)", expanded=False):
            st.markdown(_CUSTOMER_WHITEPAPER_MD)

        eff_total = _timeline_efficiency_score_from_tl_plot(tl_plot)
        eff_day_prev = e_d - timedelta(days=1)
        eff_color = "#10B981" if eff_total >= 80 else ("#F59E0B" if eff_total >= 50 else "#EF4444")

        # ==========================================
        # [UI 개선] 점수 카드와 트렌드 카드를 분리하고 기간 설정 추가
        # ==========================================
        c_score, c_spark = st.columns([1, 1.5])

        # 1. 좌측: 메인 점수 카드
        with c_score:
            with st.container(border=True):
                st.caption(
                    f"🎯 **AI 광고 효율 총점** · 최근 48시간({eff_day_prev.month}/{eff_day_prev.day} ~ {e_d.month}/{e_d.day})"
                )
                _eff_bar_pct = max(0.0, min(float(eff_total), 100.0))
                st.markdown(
                    f"""
                    <div style="display: flex; flex-direction: column; justify-content: center; padding: 15px 0;">
                        <p style="margin:0; font-size:2.8rem; font-weight:800; color:{eff_color}; line-height:1;">{eff_total:.1f}점</p>
                        <div style="width: 100%; background-color: #E2E8F0; border-radius: 999px; height: 8px; margin-top: 15px; overflow: hidden;">
                            <div style="background-color: {eff_color}; width: {_eff_bar_pct}%; height: 8px; border-radius: 999px;"></div>
                        </div>
                        <p style="margin-top:8px; font-size:0.75rem; color:#94A3B8;">* 심야 시간(00:00~08:00) 평가 제외</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # 2. 우측: 스파크라인 트렌드 카드 (최근 2주 고정, 높이 좌측 카드와 맞춤)
        with c_spark:
            with st.container(border=True):
                # 1. 셀렉트 박스 완전 제거 및 헤더만 유지
                st.caption("📈 **일간 점수 트렌드 (최근 2주)**")

                # 2. 기간 2주(14일) 고정
                spark_start = max(e_d - timedelta(days=13), start_dt.date())
                dates = pd.date_range(start=spark_start, end=end_dt.date())

                trend_data = []
                for d in dates:
                    d_date = d.date()
                    t_tl, _, _ = _clip_timeline_to_chart_day(timeline_df, d_date)
                    sc = _timeline_efficiency_score_from_tl_plot(t_tl)
                    trend_data.append({"날짜": d_date, "점수": sc})
                df_trend = pd.DataFrame(trend_data)
                if df_trend.empty:
                    df_trend = pd.DataFrame([{"날짜": e_d, "점수": float(eff_total)}])

                # 3. 차트 렌더링 및 높이(Height) 강제 축소
                fig_spark = px.line(df_trend, x="날짜", y="점수", markers=True)
                fig_spark.update_traces(
                    line_color="#EF4444",
                    marker=dict(size=6, color="white", line=dict(color="#EF4444", width=2)),
                )
                fig_spark.update_layout(
                    margin=dict(l=10, r=10, t=10, b=10),
                    xaxis=dict(
                        title="날짜",
                        visible=True,
                        showgrid=False,
                        fixedrange=True,
                        tickformat="%m/%d",
                    ),
                    yaxis=dict(
                        title="점수",
                        visible=True,
                        showgrid=True,
                        gridcolor="#F1F5F9",
                        range=[0, 105],
                        fixedrange=True,
                        dtick=25,
                    ),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    height=110,
                    hovermode="x unified",
                )
                st.plotly_chart(fig_spark, use_container_width=True, config={"displayModeBar": False})

        n_total = len(action_df_48h) if not action_df_48h.empty else 0
        n_ok = int((action_df_48h["상태"] == "✅ 방어 중").sum()) if not action_df_48h.empty else 0
        n_bad = int((action_df_48h["상태"] == "❌ 효력 종료").sum()) if not action_df_48h.empty else 0

        st.markdown(
            f"""
            <div style="display: flex; gap: 15px; margin-top: 15px; margin-bottom: 30px; flex-wrap: wrap;">
                <div style="flex: 1; min-width: 200px; background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 12px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                    <div style="color: #64748B; font-size: 14px; font-weight: 600; margin-bottom: 8px;">총 관리 매물 수</div>
                    <div style="color: #0F172A; font-size: 32px; font-weight: 800;">{n_total:,}</div>
                </div>
                <div style="flex: 1; min-width: 200px; background: #ECFDF5; border: 1px solid #D1FAE5; border-radius: 12px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                    <div style="color: #065F46; font-size: 14px; font-weight: 600; margin-bottom: 8px;">🟢 현재 상위권인 매물 수</div>
                    <div style="color: #047857; font-size: 32px; font-weight: 800;">{n_ok:,}</div>
                </div>
                <div style="flex: 1; min-width: 200px; background: #FEF2F2; border: 1px solid #FEE2E2; border-radius: 12px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                    <div style="color: #991B1B; font-size: 14px; font-weight: 600; margin-bottom: 8px;">🔴 현재 상위권에서 탈락한 매물 수</div>
                    <div style="color: #B91C1C; font-size: 32px; font-weight: 800;">{n_bad:,}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
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

            def get_sort_key(t):
                info = sort_info.get(t)
                return (info[0], -info[1], t) if info else ("", 0, t)

            task_order = sorted(unique_tasks, key=get_sort_key)
            ticktext_list = [
                f"{t} · 🔄 {sort_info[t][1]}회 · 🕒 {sort_info[t][2]}&nbsp;&nbsp;&nbsp;&nbsp;"
                for t in task_order
            ]

            # ==========================================
            # [수정] 🚀 매물별 실시간 감시망 (Live Tracker)
            # ==========================================
            kst_now = _now_kst_naive()
            kst_today = kst_now.date()

            # 원본 데이터 및 내 매물(Task) 리스트 확보
            t_df = filtered_df[filtered_df["단지명"] == _sel_complex].copy()
            if not t_df.empty and "Task" not in t_df.columns:
                t_df["Task"] = t_df.apply(
                    lambda r: _task_label_from_spec(
                        r.get("단지명", ""),
                        r.get("동/호수", ""),
                        r.get("층/타입", ""),
                        _scalar_price_str(r.get("가격", "")),
                    ),
                    axis=1,
                )

            my_unified = clean_realtor_name(filter_realtor_name)
            t_df["부동산명_통합"] = t_df["부동산명"].apply(clean_realtor_name)

            # 현재 화면에 보이는 내 활동 매물 위주로 리스트업 (유령 매물: 28일 내 갱신 2건 미만 제외)
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

            _raw_tracker_tasks = tl_plot["Task"].dropna().unique().tolist() if not tl_plot.empty else []
            active_tasks = [
                t
                for t in _raw_tracker_tasks
                if _renewal_events_28d_for_task(str(t), t_df, kst_today) >= 2
            ]

            if active_tasks:
                st.markdown("---")
                # [수정] 제목 폰트 확대
                st.markdown(
                    f"<h3 style='color:#1E293B; margin-bottom: 0px;'>👀 실시간 타점 감시망 <span style='font-size: 1.2rem; color: #64748B;'>(기준: {kst_now.strftime('%H:%M')})</span></h3>",
                    unsafe_allow_html=True,
                )

                # [수정] 선택창 라벨 폰트 확대 (기본 라벨은 숨김)
                st.markdown(
                    "<div style='font-size: 1.15rem; font-weight: 700; color: #334155; margin-top: 18px; margin-bottom: 5px;'>🎯 감시할 내 매물을 선택하세요:</div>",
                    unsafe_allow_html=True,
                )
                st.markdown("<br>", unsafe_allow_html=True)
                sel_task = st.selectbox(
                    label="감시할 매물 선택",
                    label_visibility="collapsed",
                    options=active_tasks,
                    key="live_tracker_task_select",
                )

                # 2. 선택된 매물의 경쟁사 — 상위권 판별만 묶음 단위로, 갱신·패턴은 단지 전역 boosted/comp
                sub_df = t_df[t_df["Task"] == sel_task].copy()

                if not sub_df.empty:
                    b_df = complex_data.get("boosted")
                    if b_df is not None and not b_df.empty:
                        b_df = b_df.copy()
                        b_df["수집일시"] = pd.to_datetime(b_df["수집일시"], errors="coerce")
                        if "부동산명_정제" not in b_df.columns:
                            b_df["부동산명_정제"] = b_df["부동산명"].apply(clean_realtor_name)
                        today_renewed = b_df[b_df["수집일시"].dt.date == kst_today][
                            "부동산명_정제"
                        ].unique().tolist()
                        b_freq = b_df.dropna(subset=["수집일시"])
                        analysis_days = max(
                            1,
                            (b_freq["수집일시"].max().date() - b_freq["수집일시"].min().date()).days + 1,
                        )
                        renew_counts = b_freq.groupby("부동산명_정제", dropna=False).size()
                        high_freq_unified = [
                            r for r, cnt in renew_counts.items() if (analysis_days / cnt) <= 4.0
                        ]
                    else:
                        today_renewed = []
                        high_freq_unified = []

                    # [타겟 1] 상위권 (최근 묶음내순위 1~3위) — 선택 매물 스냅샷만 사용
                    latest_ranks = sub_df.groupby("부동산명_통합")["묶음내순위_숫자"].last().reset_index()
                    latest_ranks["묶음내순위_숫자"] = (
                        pd.to_numeric(latest_ranks["묶음내순위_숫자"], errors="coerce").fillna(999)
                    )
                    top3_unified = latest_ranks[latest_ranks["묶음내순위_숫자"] <= 3]["부동산명_통합"].tolist()

                    sub_realtors = sub_df["부동산명_통합"].unique().tolist()
                    high_freq_unified = [r for r in high_freq_unified if r in sub_realtors]

                    target_status = {}
                    for r_uni in set(top3_unified + high_freq_unified):
                        if r_uni == my_unified:
                            continue

                        r_original_series = sub_df[sub_df["부동산명_통합"] == r_uni]["부동산명"]
                        if r_original_series.empty:
                            continue
                        r_original = r_original_series.iloc[-1]
                        r_disp = mask_text(
                            r_original,
                            is_demo=IS_DEMO_MODE,
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
                                if "오늘 요일 주력 시간" in cm.columns and pd.notna(
                                    row0.get("오늘 요일 주력 시간")
                                ):
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
                                pattern_desc = (
                                    f"{wd_disp} 패턴: 데이터 없음 (전체 기준 {peak_usual_n})"
                                )
                            else:
                                pattern_desc = f"{wd_disp} 패턴: 데이터 없음 (평일 기준 분석)"
                        elif peak_usual_n in _bad_peak or peak_today_n in _bad_peak:
                            pattern_desc = "패턴 데이터 부족"
                        elif peak_usual_n == peak_today_n:
                            pattern_desc = f"평소처럼 {peak_today_n}에 집중합니다"
                        else:
                            wg = f"{wd_disp} " if wd_disp else ""
                            pattern_desc = f"평소 {peak_usual_n} ➔ {wg}{peak_today_n} 위주"

                        # 3행: 상태 / 패턴 / 마지노선 (가독성 고정 포맷)
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

                    # ===========================================================
                    # [최상단] 통합 타격 지시 카드 (Integrated Action Card)
                    # ===========================================================
                    strategy_dict = complex_data.get("strategy_dict", {})
                    ai_msg = strategy_dict.get(sel_task, "") or ""
                    any_waiting = (
                        any(info["is_waiting"] for info in target_status.values())
                        if target_status
                        else False
                    )

                    action = _determine_action_state(
                        target_status=target_status,
                        any_waiting=any_waiting,
                        ai_msg=ai_msg,
                        kst_now=kst_now,
                    )
                    waiting_cnt_card = sum(
                        1 for info in target_status.values() if info.get("is_waiting")
                    )
                    _render_action_card(
                        action,
                        ai_msg,
                        total_watch=len(target_status),
                        waiting_watch=waiting_cnt_card,
                    )

                    # ===========================================================
                    # [보조 정보] 감시 중인 경쟁사 상세 — 작게 나열
                    # ===========================================================
                    if target_status:
                        st.markdown(
                            "<div style='font-size:1.0rem;font-weight:700;color:#334155;"
                            "margin-top:6px;margin-bottom:4px;'>"
                            f"👁️ 감시 중인 경쟁사 상세 "
                            f"<span style='font-size:0.85rem;font-weight:500;color:#64748b;'>"
                            f"([{sel_task}] 기준 · {len(target_status)}곳)"
                            f"</span></div>",
                            unsafe_allow_html=True,
                        )
                        st.caption(
                            "※ 광고 여부는 단지 전체 기준으로 감시하며, "
                            "현재 선택한 매물을 보유한 부동산만 표시됩니다."
                        )

                        # 대기중(위험) → 안전 순 정렬
                        sorted_targets = sorted(
                            target_status.items(), key=lambda x: not x[1]["is_waiting"]
                        )

                        cols = st.columns(min(len(target_status), 4))
                        col_idx = 0
                        for r_uni, info in sorted_targets:
                            if info.get("is_waiting"):
                                _card_bg = "#eff6ff"
                            elif info.get("is_done_today"):
                                _card_bg = "#ecfdf5"
                            else:
                                _card_bg = "#f1f5f9"
                            _title = info.get("display_short") or html.escape(
                                str(info.get("display", ""))
                            )
                            _freq_e = html.escape(str(info.get("freq", "")))
                            cols[col_idx % len(cols)].markdown(
                                f"<div style='height:180px; overflow-y:hidden; display:flex; "
                                f"flex-direction:column; justify-content:space-between; padding:15px; "
                                f"border-radius:8px; border:1px solid #ddd; background-color:{_card_bg}; "
                                f"margin-bottom:10px; box-sizing:border-box;'>"
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
                            col_idx += 1
            elif _raw_tracker_tasks:
                st.markdown("---")
                st.markdown(
                    f"<h3 style='color:#1E293B; margin-bottom: 6px;'>👀 실시간 타점 감시망</h3>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    "<div style='height:180px; overflow-y:hidden; display:flex; align-items:center; "
                    "justify-content:center; padding:15px; border-radius:8px; border:1px solid #ddd; "
                    "background-color:#f8fafc; color:#475569; font-size:0.95rem; line-height:1.55; "
                    "text-align:center; box-sizing:border-box;'>"
                    "<div>최근 28일 내 갱신 이력이 <strong>2건 미만</strong>인 매물(유령 부동산)은 "
                    "실시간 감시망 선택 목록에서 제외됩니다. 타임라인에 노출되는 다른 매물을 확인하거나 "
                    "데이터 수집을 더 진행해 주세요.</div></div>",
                    unsafe_allow_html=True,
                )

            # ==========================================
            # 아래부터는 기존 px.timeline 그리는 코드 (display_tl_df 치환 없이 원본 tl_hover 사용)
            # 3. 차트 기본 렌더링 (툴팁: 내 순위·1위 부동산 마스킹, 호버 프레임은 캐시)
            tl_hover = _build_plotly_hover_frame(
                tl_plot,
                IS_DEMO_MODE,
                filter_realtor_name,
                display_realtor,
            )
            tl_hover = tl_hover[tl_hover["Task"].isin(task_order)].copy()
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
                categoryarray=task_order,
                tickmode="array",
                tickvals=task_order,
                ticktext=ticktext_list,
                tickfont=dict(family=_plot_font, size=11, color=_toss_body),
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
                height=max(500, len(task_order) * 35),
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
                for _h0, _m0, _h1, _m1 in _peak_slots:
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
                    fig.add_annotation(
                        x=_pk0 + (_pk1 - _pk0) / 2,
                        xref="x",
                        y=0.98,
                        yref="paper",
                        text="트래픽 집중",
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

            # 7. AI 추천 시각 마커 — 별(star) 통일, 1순위 빨강 / 2순위 주황 (Task별 광고 추천 시간)
            mx1, my1, m_adv1 = [], [], []
            mx2, my2, m_adv2 = [], [], []
            for t in task_order:
                r = sort_info[t][3]
                if r is None:
                    continue
                advice = r.get("광고 추천 시간")
                advice_s = str(advice or "")
                ts1 = _ai_rec_ts_in_48h_window(advice_s, e_d, day_start, day_end)
                if ts1:
                    mx1.append(ts1)
                    my1.append(t)
                    m_adv1.append(advice_s)
                ts2 = _ai_secondary_ts_in_48h_window(advice_s, e_d, day_start, day_end)
                if ts2:
                    mx2.append(ts2)
                    my2.append(t)
                    m_adv2.append(advice_s)

            _ai_star_1 = "#EF4444"
            _ai_star_2 = "#F59E0B"
            if mx1:
                fig.add_trace(go.Scatter(
                    x=mx1,
                    y=my1,
                    mode="markers",
                    marker=dict(
                        symbol="star",
                        size=13,
                        color=_ai_star_1,
                        line=dict(color="white", width=1.5),
                    ),
                    customdata=m_adv1,
                    name="AI 1순위 추천",
                    hovertemplate="%{y}<br>%{customdata}<extra></extra>",
                ))

            if mx2:
                fig.add_trace(go.Scatter(
                    x=mx2,
                    y=my2,
                    mode="markers",
                    marker=dict(
                        symbol="star",
                        size=13,
                        color=_ai_star_2,
                        line=dict(color="white", width=1.5),
                    ),
                    customdata=m_adv2,
                    name="AI 2순위 추천",
                    hovertemplate="%{y}<br>%{customdata}<extra></extra>",
                ))

            st.markdown(
                "<div style='font-size:0.88rem;color:#475569;margin:4px 0 8px 0;line-height:1.55;'>"
                "⭐ <span style='color:#EF4444;font-weight:600;'>빨간색 별</span>: 1순위 타격 추천 시각 / "
                "⭐ <span style='color:#F59E0B;font-weight:600;'>주황색 별</span>: "
                "2순위 타격 추천 시각 (오전·오후 분리)"
                "</div>",
                unsafe_allow_html=True,
            )
            # [혁신적 우회법] Streamlit의 80KB 청크 절단 버그를 원천 봉쇄
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



    if True:
        st.markdown("<br><br>", unsafe_allow_html=True)
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

main()
