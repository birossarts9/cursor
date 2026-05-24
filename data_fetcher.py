import os
import re
import time
from datetime import datetime, timedelta, timezone

import gspread
import numpy as np

# 스크립트 위치 기준 절대 경로 (실행 cwd와 무관)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = _BASE_DIR  # "data" 폴더 지정을 빼고 최상위 폴더로 바로 연결
import pandas as pd
import streamlit as st

_SERVICE_ACCOUNT_KEY_PATH = os.path.join(_BASE_DIR, "service_account_key.json")


def _sterilize_text(val):
    if not isinstance(val, str):
        return val

    # 1. JSON 이스케이프 에러 주범 교체
    val = val.replace("\\", "/")

    # 2. [핵심] Streamlit 청크 분할·깨진 유니코드 대체 문자(U+FFFD) 원천 삭제
    val = val.replace("\ufffd", "")

    # 3. [핵심] Plotly 툴팁(hovertemplate)의 HTML 태그(<b> 등)와 충돌 방지: 꺾쇠를 둥근 괄호로
    val = val.replace("<", "(").replace(">", ")")

    # 4. 눈에 안 보이는 제어 문자(0~31번) 제거
    val = "".join(ch for ch in val if ord(ch) >= 32 or ch in "\n\r\t")

    # 5. 강제 인코딩/디코딩으로 찌꺼기 유니코드 바이트 소각
    val = val.encode("utf-8", "ignore").decode("utf-8", "ignore")

    return val.strip()


def _parse_floor_type(s):
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return ""
    m_dir = re.search(r"(남동향|남서향|북동향|북서향|동향|서향|남향|북향)", s)
    direction = m_dir.group(1) if m_dir else ""
    s_no_dir = re.sub(r"(남동향|남서향|북동향|북서향|동향|서향|남향|북향)", "", s).strip()
    parts = [p.strip() for p in s_no_dir.split() if p.strip()]
    area, floor = "", ""
    if len(parts) >= 2:
        for p in parts:
            if "층" in p or "저/" in p or "중/" in p or "고/" in p:
                floor = p
            elif re.search(r"[a-zA-Z㎡]", p):
                area = p
        if not floor and not area:
            floor, area = parts[0], parts[1]
        elif floor and not area:
            area = parts[0] if parts[1] == floor else parts[1]
        elif area and not floor:
            floor = parts[0] if parts[1] == area else parts[1]
    elif len(parts) == 1:
        p = parts[0]
        if "층" in p or "저/" in p or "중/" in p or "고/" in p:
            floor = p
        elif re.search(r"[a-zA-Z㎡]", p):
            area = p
        else:
            area = p
    if floor and not floor.endswith("층") and re.match(r"^[0-9저중고]+/[0-9]+$", floor):
        floor += "층"
    return f"{area}|{floor}|{direction}"


@st.cache_resource(show_spinner=False)
def get_gspread_client():
    """프로젝트 루트의 service_account_key.json으로 gspread 인증."""
    return gspread.service_account(filename=_SERVICE_ACCOUNT_KEY_PATH)


def load_realtor_map():
    """구글 스프레드시트의 '회원명부' 탭에서 실시간 회원 리스트, 만료일, 단지 권한을 로드합니다."""
    try:
        client = get_gspread_client()
        # 기존 사용 중인 스프레드시트 마스터 ID 고정
        sheet_id = "1yEllJWWNwsd5FMvvgwSIvA46j10XU_8MxpRAWcs-ba8"
        doc = client.open_by_key(sheet_id)

        # '회원명부' 워크시트 로드
        worksheet = doc.worksheet("회원명부")
        records = worksheet.get_all_records()

        realtor_map = {}
        for row in records:
            # 필수 컬럼 검증 및 전처리
            cid = str(row.get("고객ID", "")).strip()
            if not cid:
                continue

            # 단지리스트를 콤마 기준으로 분리하여 배열화
            raw_complexes = str(row.get("단지리스트", "")).strip()
            complex_list = [c.strip() for c in raw_complexes.split(",") if c.strip()] if raw_complexes else []

            realtor_map[cid] = {
                "name": str(row.get("부동산명", "테스트 부동산")).strip(),
                "complexes": complex_list,
                "expire_date": str(row.get("만료일", "2026-01-01")).strip(),
            }

        # 기본 데모 계정 방어막 유지
        if "demo" not in realtor_map:
            realtor_map["demo"] = {
                "name": "체험용 부동산",
                "complexes": ["사랑동행복단지"],
                "expire_date": "2029-12-31",
            }
        return realtor_map
    except Exception as e:
        print(f"[ERROR] 구글 시트 회원명부 로드 실패: {e}")
        # 실패 시 시스템이 다운되지 않도록 최소한의 안전 데모 마스터 반환
        return {
            "demo": {
                "name": "체험용 부동산",
                "complexes": ["사랑동행복단지"],
                "expire_date": "2029-12-31",
            }
        }


@st.cache_data(ttl=600, max_entries=1, show_spinner=False)
def load_renewal_logs():
    try:
        client = get_gspread_client()
        sheet_id = "1yEllJWWNwsd5FMvvgwSIvA46j10XU_8MxpRAWcs-ba8"
        doc = client.open_by_key(sheet_id)
        df_exec = pd.DataFrame(doc.worksheet("실행로그").get_all_values())
        return df_exec
    except Exception:
        return pd.DataFrame()


def clean_realtor_name(name):
    pattern = r"공인중개사사무소|공인중개사|중개사무소|부동산|중개사|공인|중개|사무소"
    cleaned = re.sub(pattern, "", str(name))
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned if cleaned else str(name)


def normalize_dong_ho(value, complex_name=""):
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return ""
    c = str(complex_name).strip()
    if c and s.startswith(c):
        s = s[len(c) :].strip()
    return s


def normalize_price_to_krw(value):
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none"}:
        return ""
    # already numeric-like
    if re.fullmatch(r"[0-9,]+", s):
        return str(int(s.replace(",", "")))

    # examples: "9억 8,000", "9억", "9억8,000"
    compact = s.replace(" ", "")
    m = re.match(r"(?P<eok>\d+)억(?P<rest>[\d,]+)?", compact)
    if m:
        eok = int(m.group("eok"))
        rest = m.group("rest")
        man = int(rest.replace(",", "")) if rest else 0
        return str(eok * 100_000_000 + man * 10_000)

    # fallback: keep numeric chars if present
    digits = re.sub(r"[^0-9]", "", s)
    return str(int(digits)) if digits else s


def normalize_floor_type(value):
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return ""

    s = re.sub(r"\s+", " ", s)

    # 면적 추출 (예: 113A/84A, 113/84m²)
    area = ""
    m_area = re.search(r"(\d+(?:\.\d+)?[A-Z]?)\s*/\s*(\d+(?:\.\d+)?[A-Z]?)(?:\s*(m²|m2))?", s, flags=re.IGNORECASE)
    if m_area:
        left = m_area.group(1).upper()
        right = m_area.group(2).upper()
        suffix = (m_area.group(3) or "").lower()
        area = f"{left}/{right}{'m²' if suffix in {'m²', 'm2'} else ''}"

    # 층수 추출 (예: 6/29, 저/29층, 중/25층)
    floor = ""
    m_floor = re.search(r"(저|중|고)?\s*/?\s*(\d+)\s*층", s)
    if m_floor:
        prefix = (m_floor.group(1) or "").strip()
        total = m_floor.group(2)
        floor = f"{prefix + '/' if prefix else ''}{total}층"
    else:
        m_floor2 = re.search(r"(\d+)\s*/\s*(\d+)(?:층)?", s)
        if m_floor2:
            floor = f"{m_floor2.group(1)}/{m_floor2.group(2)}층"

    # 방향 추출 (예: 남향, 남동향)
    direction = ""
    m_dir = re.search(r"(남동향|남서향|북동향|북서향|동향|서향|남향|북향)", s)
    if m_dir:
        direction = m_dir.group(1)

    # 규격 강제: 면적|층수|방향
    return f"{area}|{floor}|{direction}"


def parse_confirm_date_flexible(value):
    s = str(value).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        return pd.NaT
    # old format: 26.03.26
    d_old = pd.to_datetime(s, format="%y.%m.%d", errors="coerce")
    if pd.notna(d_old):
        return d_old
    # new format: 2026-04-25 (and similar ISO dates)
    return pd.to_datetime(s, errors="coerce")


@st.cache_data(ttl=3600, max_entries=1, show_spinner=False)
def process_data(df):
    start_t = time.time()
    print("[START] process_data")
    df = df.copy()

    df["수집일시"] = pd.to_datetime(df["수집일시"])
    df = df.sort_values(["단지명", "수집일시"])

    time_diff_mins = df.groupby("단지명")["수집일시"].diff().dt.total_seconds() / 60.0
    df["새_세션"] = (time_diff_mins > 40) | time_diff_mins.isna()
    df["세션ID"] = df.groupby("단지명")["새_세션"].cumsum()

    session_rep = (
        df.groupby(["단지명", "세션ID"])["수집일시"]
        .min()
        .dt.floor("min")
        .reset_index(name="대표수집일시")
    )
    df = pd.merge(df, session_rep, on=["단지명", "세션ID"], how="left")
    df["수집일시"] = df["대표수집일시"]
    df = df.sort_values("수집일시")

    session_times = df["수집일시"].drop_duplicates().sort_values()
    gap_check = session_times.diff().dt.total_seconds() / 3600.0
    gap_starts = session_times[gap_check > 2.5].tolist()

    df["왜곡영역"] = False
    for start_time in gap_starts:
        df.loc[
            (df["수집일시"] >= start_time)
            & (df["수집일시"] < start_time + timedelta(hours=1)),
            "왜곡영역",
        ] = True

    df["전체순위_숫자"] = (
        pd.to_numeric(
            df["전체순위"].astype(str).str.extract(r'(\d+)')[0],
            errors="coerce",
        )
        .fillna(999)
        .astype(int)
    )
    df["묶음내순위_숫자"] = (
        pd.to_numeric(
            df["묶음내순위"].astype(str).str.replace("단독", "1").str.extract(r'(\d+)')[0],
            errors="coerce",
        )
        .fillna(999)
        .astype(int)
    )

    for col in ["동/호수", "층/타입", "거래방식", "가격", "단지명"]:
        if col in df.columns:
            df[col] = df[col].fillna("")

    # 크롤러 신/구 포맷 혼재 대응: 스키마를 공통 포맷으로 정규화 (벡터화)
    # 동/호수: 공백/결측 정리
    df["동/호수"] = (
        df["동/호수"].astype(str).str.strip().replace({"nan": "", "None": ""})
    )

    df["층/타입"] = df["층/타입"].apply(_parse_floor_type)

    # 가격 정규화 (숫자/억 단위/기타 문자열 벡터 변환)
    price_src = df["가격"].astype(str).str.strip()
    price_src = price_src.replace({"nan": "", "None": ""})
    num_like = price_src.str.fullmatch(r"[0-9,]+", na=False)
    eok_ext = price_src.str.replace(" ", "", regex=False).str.extract(r"(?i)(\d+)억([\d,]+)?")
    eok = pd.to_numeric(eok_ext[0], errors="coerce").fillna(0)
    man = pd.to_numeric(eok_ext[1].fillna("0").str.replace(",", "", regex=False), errors="coerce").fillna(0)
    eok_val = (eok * 100_000_000 + man * 10_000).astype("Int64").astype(str).replace("<NA>", "")
    digits = price_src.str.replace(r"[^0-9]", "", regex=True)
    digits_val = pd.to_numeric(digits, errors="coerce").astype("Int64").astype(str).replace("<NA>", "")
    numeric_val = price_src.str.replace(",", "", regex=False)
    df["가격"] = np.where(
        num_like,
        numeric_val,
        np.where(eok_ext[0].notna(), eok_val, digits_val),
    )

    df["확인일자"] = df["확인일자"].astype(str).str.strip().replace({"nan": pd.NA, "None": pd.NA, "": pd.NA})
    df["확인일자_Date"] = pd.to_datetime(df["확인일자"], errors="coerce")

    if "고유번호" not in df.columns:
        df["고유번호"] = "기록없음"
    df["고유번호"] = df["고유번호"].fillna("기록없음")

    if "CP사" not in df.columns:
        df["CP사"] = ""
    df["CP사"] = df["CP사"].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})

    # 가격 변동으로 같은 매물이 분절되지 않도록 가격은 키에서 제외 (CP사는 채널 분리)
    df["매물묶음키"] = (
        df["동/호수"].astype(str).str.strip()
        + " | "
        + df["층/타입"].astype(str).str.strip()
        + " | "
        + df["거래방식"].astype(str).str.strip()
        + " | "
        + df["CP사"].astype(str).str.strip()
    )
    cleaned_name = (
        df["부동산명"]
        .astype(str)
        .str.replace(r"공인중개사사무소|공인중개사|중개사무소|부동산|중개사|공인|중개|사무소", "", regex=True)
        .str.replace(r"\s+", "", regex=True)
    )
    df["_temp_name"] = cleaned_name.where(cleaned_name.ne(""), df["부동산명"].astype(str))
    df = df.sort_values(["수집일시", "전체순위_숫자"], ascending=[True, True])
    # subset에서 매물묶음키 대신 본질적 스펙과 중개사명만 지정하여 멀티 CP 도배를 1건으로 압축
    df = df.drop_duplicates(subset=["수집일시", "동/호수", "층/타입", "거래방식", "_temp_name"], keep="first")
    df = df.drop(columns=["_temp_name"])
    print(f"[DONE] process_data ({time.time() - start_t:.2f}s)")
    return df


@st.cache_data(ttl=600, max_entries=1, show_spinner=False)
def load_server_data():
    start_t = time.time()
    print("[START] load_server_data")
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)

    base_names = []

    base_names.append(f"naver_market_report_{now.strftime('%Y_%m')}")
    # 최대 30일 범위를 안전하게 커버하기 위해 직전 월도 항상 로드 후보에 포함
    last_month = now.replace(day=1) - timedelta(days=1)
    base_names.append(f"naver_market_report_{last_month.strftime('%Y_%m')}")
    base_names.append("data")

    df_list = []
    for base_name in base_names:
        parquet_path = os.path.join(DATA_DIR, f"{base_name}.parquet")
        excel_path = os.path.join(DATA_DIR, f"{base_name}.xlsx")
        try:
            if os.path.exists(parquet_path):
                df_list.append(pd.read_parquet(parquet_path))
            elif os.path.exists(excel_path):
                df_list.append(pd.read_excel(excel_path))
        except Exception:
            pass

    if not df_list:
        print(f"[WARN] load_server_data no files ({time.time() - start_t:.2f}s)")
        return None

    df = pd.concat(df_list, ignore_index=True).drop_duplicates()
    cutoff_date = pd.to_datetime("today") - pd.Timedelta(days=14)
    df["수집일시"] = pd.to_datetime(df["수집일시"], errors="coerce")
    df = df[df["수집일시"].notna() & (df["수집일시"] >= cutoff_date)]
    print(f"[DONE] load_server_data monthly rows={len(df):,} ({time.time() - start_t:.2f}s)")
    return df
