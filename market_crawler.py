import os
import sys
import time
import threading
import datetime
import random
import urllib.request
import urllib.error
import http.cookiejar
import json
import base64
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- [상수 및 경로 설정] ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SWEEP_MEMORY_FILE = os.path.join(BASE_DIR, "my_sweep_list.json")
VIP_MEMORY_FILE = os.path.join(BASE_DIR, "my_vip_list.json")
NAVER_FIN_ARTICLE_LIST_API = "https://fin.land.naver.com/front-api/v1/complex/article/list"
TARGET_ANALYZE = 100
OPERATING_START_HOUR = 8
OPERATING_END_HOUR = 24
LEGACY_COL_ORDER = ["매물번호", "수집일시", "단지명", "전체순위", "노출형태", "묶음내순위", "부동산명", "동/호수", "거래방식", "가격", "층/타입", "확인일자", "고유번호", "CP사"]
TRADE_TYPE_LABELS = {
    "A1": "매매",
    "B1": "전세",
    "B2": "월세",
    "B3": "단기",
}
DIRECTION_LABELS = {
    "SS": "남향",
    "NN": "북향",
    "EE": "동향",
    "WW": "서향",
    "WE": "서향",
    "ES": "남동향",
    "SE": "남동향",
    "WS": "남서향",
    "SW": "남서향",
    "EN": "북동향",
    "NE": "북동향",
    "WN": "북서향",
    "NW": "북서향",
}

# ======================================================
# 🧠 [마스터 통제실] 구글 시트 중앙 관제 시스템
# ======================================================
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    key_path = os.path.join(BASE_DIR, "service_account_key.json")
    creds = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
    return gspread.authorize(creds)

def load_control_room():
    """크롤러 기동 시 [통제실] 탭을 읽어와서 딕셔너리로 메모리에 올림"""
    try:
        client = get_gspread_client()
        sheet = client.open_by_key("1yEllJWWNwsd5FMvvgwSIvA46j10XU_8MxpRAWcs-ba8").worksheet("통제실")
        records = sheet.get_all_records()

        control_data = {}
        for row in records:
            on_off = str(row.get('자동방어 ON/OFF', '')).strip().upper()
            if on_off != 'OFF':
                # 🚨 불필요한 '전체순위', '노출형태' 등은 무시하고 5개 스펙만 가져옴
                danji = str(row.get('단지명', '')).strip()
                dongho = str(row.get('동/호수', '')).strip()
                floor_type = str(row.get('층/타입', '')).strip()
                trade_type = str(row.get('거래방식', '')).strip()
                price = str(row.get('가격', '')).strip()
                
                # 스펙 5종 세트로 절대 변하지 않는 지문(Key) 생성
                spec_key = f"{danji}|{dongho}|{floor_type}|{trade_type}|{price}"
                
                if danji and dongho:
                    # 엑셀 헤더가 '부동산'일 수도, '부동산명'일 수도 있으니 둘 다 커버
                    realtor_name = str(row.get('부동산명', '') or row.get('부동산', '')).strip()
                    
                    control_data[spec_key] = {
                        '부동산명': realtor_name,
                        '아이디': str(row.get('아이디', '')),
                        '비밀번호': str(row.get('비밀번호', '')),
                        '타입': str(row.get('타입(VVIP/VIP)', row.get('타입 (VVIP/VIP)', ''))),
                        '방어마지노선': int(row.get('방어마지노선', 0) or row.get('방어마지노선 순위', 0) or 0),
                        '쿨타임': float(row.get('쿨타임', 0) or 0),
                        '최근갱신일시': str(row.get('최근갱신일시', ''))
                    }
        return control_data
    except Exception as e:
        print(f"🚨 통제실 로드 실패: {e}")
        return {}

def append_to_sheet(sheet_name, data_row):
    """탭 이름을 명시하여 데이터 기록 (순위로그, 작업지시서 등)"""
    try:
        client = get_gspread_client()
        sheet = client.open_by_key("1yEllJWWNwsd5FMvvgwSIvA46j10XU_8MxpRAWcs-ba8").worksheet(sheet_name)
        sheet.append_row(data_row, value_input_option='USER_ENTERED')
    except Exception as e:
        print(f"❌ 구글 시트 전송 오류 ({sheet_name}): {e}")

# PyQt5 플러그인 안전장치
try:
    import PyQt5
    plugin_path = os.path.join(os.path.dirname(PyQt5.__file__), 'Qt5', 'plugins', 'platforms')
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = plugin_path
except ImportError:
    pass

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QTableWidget, QTableWidgetItem, 
                             QHeaderView, QTextEdit, QGroupBox, QComboBox)
from PyQt5.QtCore import pyqtSignal, QObject, QTimer, Qt

try:
    import pandas as pd
except ImportError:
    pd = None

# ======================================================
# 🚀 깃허브 자동 업로드 엔진
# ======================================================
def auto_github_push(target_filename, log_func=None):
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = "birossarts9/realestate-date-report"
    file_path = os.path.join(BASE_DIR, target_filename)
    url = f"https://api.github.com/repos/{repo}/contents/{target_filename}"

    try:
        if not token:
            if log_func: log_func("❌ 깃허브 토큰이 없습니다. GITHUB_TOKEN 환경변수를 설정하세요.")
            return

        if not os.path.exists(file_path):
            if log_func: log_func(f"❌ 업로드할 {target_filename} 파일이 없습니다.")
            return

        with open(file_path, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")

        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Python-urllib"
        }

        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        sha = None
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx) as response:
                res_data = json.loads(response.read().decode())
                sha = res_data.get("sha")
        except urllib.error.HTTPError as e:
            if e.code != 404: raise e

        data = {"message": f"Auto update: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", "content": content, "branch": "main"}
        if sha: data["sha"] = sha

        put_req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="PUT")
        with urllib.request.urlopen(put_req, context=ctx) as put_response:
            if put_response.status in [200, 201]:
                now_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if log_func: log_func(f"✅ [{now_time}] {target_filename} 전송 완료!")
                
    except Exception as e:
        if log_func: log_func(f"❌ 깃허브 전송 실패: {e}")

def upload_to_aws(target_filename, log_func=None):
    host = "52.78.67.119"
    username = "ubuntu"
    # Lightsail 접속용 .pem 키는 market_crawler.py와 같은 폴더에 둡니다.
    key_path = os.path.join(BASE_DIR, "LightsailDefaultKey-ap-northeast-2.pem")
    local_path = os.path.join(BASE_DIR, target_filename)
    remote_path = f"/home/ubuntu/cursor/{target_filename}"
    ssh = None
    sftp = None

    try:
        import importlib
        paramiko = importlib.import_module("paramiko")

        if not os.path.exists(local_path):
            if log_func: log_func(f"❌ [AWS] 업로드할 {target_filename} 파일이 없습니다.")
            return

        if not os.path.exists(key_path):
            if log_func: log_func(f"❌ [AWS] 키 파일을 찾을 수 없습니다: {key_path}")
            return

        key = paramiko.RSAKey.from_private_key_file(key_path)
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=host, username=username, pkey=key, timeout=20)

        sftp = ssh.open_sftp()
        sftp.put(local_path, remote_path)
        if log_func: log_func(f"🚀 [AWS] {target_filename} 전송 완료!")

    except ImportError:
        if log_func: log_func("❌ [AWS] paramiko 라이브러리가 설치되어 있지 않습니다. pip install paramiko 후 다시 실행하세요.")
    except Exception as e:
        if log_func: log_func(f"❌ [AWS] 전송 실패: {e}")
    finally:
        try:
            if sftp:
                sftp.close()
        except Exception as e:
            if log_func: log_func(f"⚠️ [AWS] SFTP 연결 종료 중 오류: {e}")
        try:
            if ssh:
                ssh.close()
        except Exception as e:
            if log_func: log_func(f"⚠️ [AWS] SSH 연결 종료 중 오류: {e}")

def safe_str(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()

def nested_get(data, path):
    cur = data
    for key in path.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key)
    return cur

def pick_value(sources, paths):
    for source in sources:
        if not isinstance(source, dict):
            continue
        for path in paths:
            value = nested_get(source, path)
            if value not in (None, "", []):
                return value
    return ""

def find_key(data, key_names):
    if isinstance(key_names, str):
        key_names = [key_names]
    if isinstance(data, dict):
        for key in key_names:
            if data.get(key) not in (None, "", []):
                return data.get(key)
        for value in data.values():
            found = find_key(value, key_names)
            if found not in (None, "", []):
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_key(item, key_names)
            if found not in (None, "", []):
                return found
    return ""

def format_trade_type(raw_value):
    raw = safe_str(raw_value)
    return TRADE_TYPE_LABELS.get(raw, raw)

def format_direction(raw_value):
    raw = safe_str(raw_value)
    if not raw:
        return ""
    upper_raw = raw.upper()
    return DIRECTION_LABELS.get(upper_raw, raw)

def format_price(article_info, representative_info, trade_type, slot=None):
    sources = [article_info, representative_info, slot]
    price_info = pick_value(sources, ["priceInfo"])
    if not isinstance(price_info, dict):
        return safe_str(price_info)

    trade_label = format_trade_type(trade_type)
    if trade_label in ("전세", "월세"):
        deposit = find_key(price_info, ["warrantPrice", "warrantyPrice", "depositPrice", "deposit", "warrantPrc", "dealOrWarrantPrc"])
        if deposit:
            return safe_str(deposit)

    candidates = ["price", "priceText", "priceName", "dealOrWarrantPrc", "dealPrice", "warrantPrice", "warrantyPrice", "rentPrice"]
    value = find_key(price_info, candidates)
    return safe_str(value)

def format_floor_type(article_info, representative_info):
    sources = [article_info, representative_info]
    floor = safe_str(pick_value(sources, ["articleDetail.floorInfo", "floorInfo"]))
    direction = format_direction(find_key(sources, ["direction", "directionName", "directionTypeName", "houseDirection", "articleDirection"]))
    supply = safe_str(pick_value(sources, ["spaceInfo.supplySpaceName", "supplySpaceName"]))
    exclusive = safe_str(pick_value(sources, ["spaceInfo.exclusiveSpaceName", "exclusiveSpaceName"]))
    if supply and exclusive and supply != exclusive:
        space = f"{supply}/{exclusive}"
    else:
        space = supply or exclusive

    parts = [floor]
    if direction and direction not in floor:
        parts.append(direction)
    parts.append(space)
    return " ".join([part for part in parts if part]).strip()

def format_dong_ho(article_info, representative_info):
    sources = [article_info, representative_info]
    dong = safe_str(pick_value(sources, ["dongName", "articleDetail.dongName"]))
    ho = safe_str(pick_value(sources, ["hoName", "articleDetail.hoName"]))
    if dong and ho:
        return f"{dong}/{ho}"
    return dong or ho or safe_str(pick_value(sources, ["articleName", "articleDetail.articleName"]))

def strip_direction_from_floor_type(floor_type):
    direction_words = ["남동향", "남서향", "북동향", "북서향", "남향", "북향", "동향", "서향"]
    result = safe_str(floor_type)
    for word in direction_words:
        result = result.replace(word, " ")
    return " ".join(result.split())

def format_elapsed(seconds):
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}시간 {minutes}분 {secs}초"
    if minutes:
        return f"{minutes}분 {secs}초"
    return f"{secs}초"

def is_operating_time(now=None):
    now = now or datetime.datetime.now()
    return OPERATING_START_HOUR <= now.hour < OPERATING_END_HOUR

def seconds_until_operating_start(now=None):
    now = now or datetime.datetime.now()
    next_start = now.replace(hour=OPERATING_START_HOUR, minute=0, second=0, microsecond=0)
    if now.hour >= OPERATING_START_HOUR:
        next_start += datetime.timedelta(days=1)
    return max(1, int((next_start - now).total_seconds()))

class WorkerSignals(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal()

# ======================================================
# [Worker] 시장 분석 엔진
# ======================================================
class MarketSweepWorker:
    def __init__(self, c_list, sigs, stop_event=None):
        self.c_list = c_list
        self.sigs = sigs
        self.stop_event = stop_event or threading.Event()
        self.api_cookie_jar = http.cookiejar.CookieJar()
        self.api_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.api_cookie_jar))
        self.api_session_ready = False
        self.wait_seconds = 0.0
        
    def send_log(self, m): 
        self.sigs.log.emit(m)

    def stop_requested(self):
        return self.stop_event.is_set()

    def sleep_or_stop(self, seconds):
        started_at = time.monotonic()
        stopped = self.stop_event.wait(seconds)
        self.wait_seconds += time.monotonic() - started_at
        return stopped

    def build_article_payload(self, complex_number, last_info):
        return {
            "size": 30,
            "complexNumber": safe_str(complex_number),
            "tradeTypes": ["A1", "B1", "B2"],
            "pyeongTypes": [],
            "dongNumbers": [],
            "userChannelType": "MOBILE",
            "articleSortType": "RANKING_DESC",
            "lastInfo": last_info or []
        }

    def build_fin_headers(self, complex_number):
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/json",
            "Origin": "https://fin.land.naver.com",
            "Referer": f"https://fin.land.naver.com/complexes/{complex_number}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

    def warmup_api_session(self, complex_number):
        if self.api_session_ready:
            return
        if self.stop_requested():
            raise RuntimeError("사용자 중지 요청")

        warmup_headers = self.build_fin_headers(complex_number)
        warmup_headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        warmup_headers.pop("Content-Type", None)

        for url in [
            "https://fin.land.naver.com/",
            f"https://fin.land.naver.com/complexes/{complex_number}"
        ]:
            try:
                req = urllib.request.Request(url, headers=warmup_headers, method="GET")
                self.api_opener.open(req, timeout=15).read(1024)
                if self.sleep_or_stop(random.uniform(1.0, 2.0)):
                    raise RuntimeError("사용자 중지 요청")
            except Exception:
                if self.stop_requested():
                    raise
                pass

        self.api_session_ready = True

    def post_article_list(self, payload):
        if self.stop_requested():
            raise RuntimeError("사용자 중지 요청")
        self.warmup_api_session(payload.get("complexNumber"))
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = self.build_fin_headers(payload.get("complexNumber"))

        last_error = None
        for attempt in range(3):
            if self.stop_requested():
                raise RuntimeError("사용자 중지 요청")
            try:
                req = urllib.request.Request(NAVER_FIN_ARTICLE_LIST_API, data=body, headers=headers, method="POST")
                with self.api_opener.open(req, timeout=15) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code == 403:
                    raise RuntimeError(f"네이버 API 차단 응답({e.code})")
                if e.code == 429:
                    self.api_session_ready = False
                    self.send_log("⏸️ 네이버 429 감지: 5분 강제 휴식에 들어갑니다.")
                    if self.sleep_or_stop(300):
                        raise RuntimeError("사용자 중지 요청")
                    self.warmup_api_session(payload.get("complexNumber"))
                    continue
            except Exception as e:
                last_error = e
            if self.sleep_or_stop((attempt + 1) * 2 + random.uniform(0.3, 1.0)):
                raise RuntimeError("사용자 중지 요청")
        raise RuntimeError(f"네이버 API 요청 실패: {last_error}")

    def normalize_article_row(self, slot, article_info, representative_info, complex_name, overall_rank, exposure_type, bundle_rank, now_dt):
        sources = [article_info, representative_info, slot]
        broker_info = pick_value(sources, ["brokerInfo"])
        verification_info = pick_value(sources, ["verificationInfo"])
        trade_type_raw = pick_value(sources, ["tradeType", "tradeTypeCode"])
        trade_type = format_trade_type(trade_type_raw)
        article_no = safe_str(pick_value(sources, ["articleNumber", "articleNo"]))

        if not isinstance(broker_info, dict):
            broker_info = {}
        if not isinstance(verification_info, dict):
            verification_info = {}

        row = {
            "매물번호": f"#{overall_rank:03d}",
            "수집일시": now_dt,
            "단지명": safe_str(pick_value(sources, ["complexName"])) or complex_name,
            "전체순위": str(overall_rank),
            "노출형태": exposure_type,
            "묶음내순위": str(bundle_rank),
            "부동산명": safe_str(broker_info.get("brokerageName")) or "파악불가",
            "동/호수": format_dong_ho(article_info, representative_info),
            "거래방식": trade_type,
            "가격": format_price(article_info, representative_info, trade_type, slot),
            "층/타입": format_floor_type(article_info, representative_info),
            "확인일자": safe_str(verification_info.get("articleConfirmDate")),
            "고유번호": article_no or "번호없음",
            "CP사": safe_str(broker_info.get("brokerName"))
        }
        return {col: safe_str(row.get(col, "")) for col in LEGACY_COL_ORDER}

    def handle_collected_row(self, row):
        now_dt = row["수집일시"]
        c_name = row["단지명"]
        agent_name = row["부동산명"]
        c_rank = int(row["전체순위"] or 0)
        bundle_rank = int(row["묶음내순위"] or 1)
        title = row["동/호수"]
        price_val = row["가격"]
        floor_spec = row["층/타입"]
        trade_type = row["거래방식"]
        article_no = row["고유번호"]

        if any(vip in agent_name for vip in self.vip_agents):
            self.send_log(f"📊 [VIP 매물 수집] {agent_name} - {title} ({c_rank}위)")
            append_to_sheet("순위로그", [now_dt, c_name, agent_name, c_rank, bundle_rank, title, price_val, floor_spec, article_no])

        current_spec_key = f"{c_name.strip()}|{title.strip()}|{floor_spec.strip()}|{trade_type.strip()}|{price_val.strip()}"
        fallback_floor_spec = strip_direction_from_floor_type(floor_spec)
        fallback_spec_key = f"{c_name.strip()}|{title.strip()}|{fallback_floor_spec}|{trade_type.strip()}|{price_val.strip()}"
        matched_spec_key = current_spec_key if current_spec_key in self.control_map else fallback_spec_key

        if matched_spec_key not in self.control_map:
            return

        target_info = self.control_map[matched_spec_key]
        if target_info['부동산명'] not in agent_name:
            return

        if c_rank <= target_info['방어마지노선']:
            self.send_log(f"🛡️ [방어 성공] {title} 타겟 매물은 안전권입니다.")
            return

        last_update_str = target_info['최근갱신일시']
        cooldown_hours = target_info['쿨타임']
        is_cooldown_passed = True

        if last_update_str:
            try:
                last_dt = datetime.datetime.strptime(last_update_str, "%Y-%m-%d %H:%M:%S")
                diff_hours = (datetime.datetime.now() - last_dt).total_seconds() / 3600
                if diff_hours < cooldown_hours:
                    is_cooldown_passed = False
            except:
                pass

        if is_cooldown_passed:
            self.send_log(f"🚨 [작업지시 발동] {title} 마지노선 이탈! (새 매물번호: {article_no})")
            append_to_sheet("작업지시서", [now_dt, target_info['부동산명'], target_info['아이디'], target_info['비밀번호'], matched_spec_key, article_no, "대기"])
            self.control_map[matched_spec_key]['최근갱신일시'] = now_dt
        else:
            self.send_log(f"⏳ [대기] {title} 매물이 밀려났으나 아직 쿨타임 남음.")

    def fetch_complex_rows(self, complex_number, complex_name):
        rows = []
        last_info = []
        overall_rank = 1

        while overall_rank <= TARGET_ANALYZE:
            if self.stop_requested():
                self.send_log(f"🛑 {complex_name or complex_number} 수집 중지 요청 감지")
                break
            payload = self.build_article_payload(complex_number, last_info)
            response = self.post_article_list(payload)
            result = response.get("result", {}) if isinstance(response, dict) else {}
            slots = result.get("list") or []
            if not slots:
                break

            for slot in slots:
                if self.stop_requested():
                    break
                if overall_rank > TARGET_ANALYZE:
                    break
                representative_info = slot.get("representativeArticleInfo") or {}
                duplicated_info = slot.get("duplicatedArticleInfo") or {}
                bundled_articles = duplicated_info.get("articleInfoList") or []
                now_dt = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if bundled_articles:
                    for bundle_rank, article_info in enumerate(bundled_articles, start=1):
                        row = self.normalize_article_row(slot, article_info or {}, representative_info, complex_name, overall_rank, "묶음", bundle_rank, now_dt)
                        rows.append(row)
                        self.handle_collected_row(row)
                else:
                    row = self.normalize_article_row(slot, representative_info, representative_info, complex_name, overall_rank, "단독", 1, now_dt)
                    rows.append(row)
                    self.handle_collected_row(row)

                overall_rank += 1

            last_info = result.get("lastInfo") or []
            if not result.get("hasNextPage") or not last_info:
                break
            if self.sleep_or_stop(random.uniform(3.5, 7.5)):
                self.send_log(f"🛑 {complex_name or complex_number} 페이지 대기 중 중지 요청 감지")
                break

        return rows

    def save_market_rows(self, all_data):
        if not all_data:
            self.send_log("⚠️ 저장할 수집 데이터가 없습니다.")
            return
        if pd is None:
            self.send_log("❌ pandas가 없어 엑셀/파케이 저장을 건너뜁니다.")
            return

        df_new = pd.DataFrame(all_data)
        for col in LEGACY_COL_ORDER:
            if col not in df_new.columns:
                df_new[col] = ""
        df_new = df_new[LEGACY_COL_ORDER].astype(str)

        month_str = datetime.datetime.now().strftime("%Y_%m")
        target_filename = f"naver_market_report_{month_str}.xlsx"
        target_parquet_filename = f"naver_market_report_{month_str}.parquet"
        fname = os.path.join(BASE_DIR, target_filename)
        parquet_fname = os.path.join(BASE_DIR, target_parquet_filename)

        max_retries = 5
        for attempt in range(max_retries):
            try:
                if os.path.exists(fname):
                    df_old = pd.read_excel(fname)
                    for col in LEGACY_COL_ORDER:
                        if col not in df_old.columns:
                            df_old[col] = ""
                    df_old = df_old[LEGACY_COL_ORDER].astype(str)
                    df_comb = pd.concat([df_old, df_new], ignore_index=True).drop_duplicates()
                    
                    # [안전장치] 엑셀 저장 권한 에러 처리
                    try:
                        df_comb.to_excel(fname, index=False)
                    except PermissionError:
                        counter = 1
                        while True:
                            alt_filename = f"naver_market_report_{month_str}({counter}).xlsx"
                            alt_fname = os.path.join(BASE_DIR, alt_filename)
                            try:
                                df_comb.to_excel(alt_fname, index=False)
                                target_filename = alt_filename
                                fname = alt_fname
                                self.send_log(f"⚠️ 원본 엑셀이 열려 있어 새 이름으로 저장했습니다: {target_filename}")
                                break
                            except PermissionError:
                                counter += 1
                                if counter > 20: raise  # 무한루프 방지
                                
                    df_comb.to_parquet(parquet_fname, index=False)
                else:
                    try:
                        df_new.to_excel(fname, index=False)
                    except PermissionError:
                        counter = 1
                        while True:
                            alt_filename = f"naver_market_report_{month_str}({counter}).xlsx"
                            alt_fname = os.path.join(BASE_DIR, alt_filename)
                            try:
                                df_new.to_excel(alt_fname, index=False)
                                target_filename = alt_filename
                                fname = alt_fname
                                self.send_log(f"⚠️ 원본 엑셀이 열려 있어 새 이름으로 저장했습니다: {target_filename}")
                                break
                            except PermissionError:
                                counter += 1
                                if counter > 20: raise
                    df_new.to_parquet(parquet_fname, index=False)

                save_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.send_log(f"\n💾 [{save_time_str}] 수집 및 엑셀/파케이 저장 완료! (총 {len(df_new)}행)")
                
                self.send_log("⏳ 깃허브 서버로 데이터 자동 전송 중...")
                auto_github_push(target_filename, self.send_log)
                auto_github_push(target_parquet_filename, self.send_log)
                
                self.send_log("⏳ AWS Lightsail 서버로 파케이 데이터 전송 중...")
                upload_to_aws(target_parquet_filename, self.send_log)
                break
            except Exception as e:
                self.send_log(f"   ⚠️ 엑셀/파케이 저장 실패 (재시도 {attempt+1}/{max_retries}) - 에러 원인: {e}")
                if self.sleep_or_stop(4.0):
                    break

    def run(self):
        total_started_at = time.monotonic()
        self.send_log("📡 [마스터 스케줄러] 통제실 데이터를 불러옵니다...")
        self.control_map = load_control_room()
        self.vip_agents = list(set([info['부동산명'] for info in self.control_map.values() if info['부동산명']]))

        self.send_log(f"🚀 [신버전 API 시장 분석] 스캔 시작 (감시 VIP: {', '.join(self.vip_agents)})")
        all_data = []

        try:
            for index, comp in enumerate(self.c_list):
                if self.stop_requested():
                    self.send_log("🛑 중지 요청으로 남은 단지 작업을 건너뜁니다.")
                    break

                complex_started_at = time.monotonic()
                c_id, c_name = safe_str(comp.get('id')), safe_str(comp.get('name'))
                self.send_log(f"\n🏢 [{index+1}/{len(self.c_list)}] {c_name or c_id} API 분석 중...")
                try:
                    rows = self.fetch_complex_rows(c_id, c_name)
                    all_data.extend(rows)
                    elapsed = format_elapsed(time.monotonic() - complex_started_at)
                    if self.stop_requested():
                        self.send_log(f"🛑 {c_name or c_id} 수집 중단: {len(rows)}행 / 소요 {elapsed}")
                    else:
                        self.send_log(f"✅ {c_name or c_id} 수집 완료: {len(rows)}행 / 소요 {elapsed}")
                except Exception as e:
                    elapsed = format_elapsed(time.monotonic() - complex_started_at)
                    if self.stop_requested():
                        self.send_log(f"🛑 {c_name or c_id} 수집 중단: {e} / 소요 {elapsed}")
                    else:
                        self.send_log(f"❌ {c_name or c_id} 수집 실패: {e} / 소요 {elapsed}")

                if self.stop_requested():
                    break

                processed = index + 1
                if processed < len(self.c_list):
                    rest_min = 1
                    msg = "✅ 단지 분석 완료. 기본 1분 대기 중"
                    if processed % 20 == 0:
                        rest_min = 30
                        msg = "🔥 [긴급] 20단지 도달! 30분 대규모 휴식 모드"
                    elif processed % 10 == 0:
                        rest_min = 10
                        msg = "💤 [알림] 10단지 도달! 10분간 장비 쿨다운"
                    elif processed % 3 == 0:
                        rest_min = 5
                        msg = "☕ [알림] 3단지 분석 완료! 5분간 중간 휴식"

                    total_sleep = (rest_min * 60) + random.uniform(5, 15)
                    self.send_log(f"  {msg}... ({total_sleep/60:.1f}분 후 다음 작업 시작)")
                    if self.sleep_or_stop(total_sleep):
                        self.send_log("🛑 단지 간 휴식 중 중지 요청 감지")
                        break

            self.save_market_rows(all_data)
        except Exception as e:
            self.send_log(f"❌ 전체 분석 오류: {e}")
        finally:
            total_elapsed = time.monotonic() - total_started_at
            wait_elapsed = self.wait_seconds
            work_elapsed = max(0, total_elapsed - wait_elapsed)
            self.send_log(f"⏱️ 전체 소요: {format_elapsed(total_elapsed)} (실작업 {format_elapsed(work_elapsed)} / 대기 {format_elapsed(wait_elapsed)})")
            self.sigs.finished.emit()

# ======================================================
# [GUI] 메인 윈도우 (릴레이 스케줄러 탑재)
# ======================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("이실장 Pro 시장 분석기 (신버전 API)")
        self.setGeometry(100, 100, 800, 800)
        self.setStyleSheet("""
            QWidget { font-family: 'Malgun Gothic'; font-size: 13px; color: #333d4b; }
            QMainWindow { background-color: #f2f4f6; }
            QPushButton { background-color: #ffffff; border: 1px solid #d1d6db; border-radius: 8px; padding: 10px; font-weight: bold; }
            QPushButton#primaryBtn { background-color: #3182f6; color: white; border: none; font-size: 14px;}
            QPushButton#dangerBtn { background-color: #e74c3c; color: white; border: none; font-size: 14px;}
            QTableWidget { background-color: #ffffff; border: 1px solid #e5e8eb; border-radius: 8px; }
            QTextEdit#logWindow { background-color: #1e293b; color: #f8fafc; border-radius: 10px; font-family: 'Consolas'; line-height: 1.6; }
        """)
        
        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        self.setCentralWidget(main_widget)
        
        tables_layout = QHBoxLayout()

        # [왼쪽] 분석 단지 리스트
        comp_vbox = QVBoxLayout()
        comp_vbox.addWidget(QLabel("🏢 분석 대상 단지 목록"))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["단지 번호(ID)", "단지명"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        comp_vbox.addWidget(self.table)
        comp_btn_lay = QHBoxLayout()
        self.btn_add = QPushButton("단지 추가 (+)")
        self.btn_add.clicked.connect(lambda: self.add_row(self.table, ["", ""]))
        self.btn_del = QPushButton("단지 삭제 (-)")
        self.btn_del.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        comp_btn_lay.addWidget(self.btn_add); comp_btn_lay.addWidget(self.btn_del)
        comp_vbox.addLayout(comp_btn_lay)
        tables_layout.addLayout(comp_vbox, 2)

        layout.addLayout(tables_layout)

        self.load_list()

        # 🚨 스케줄러 UI
        opt_group = QGroupBox("크롤링 옵션")
        opt_layout = QHBoxLayout(opt_group)
        self.cycle_combo = QComboBox()
        self.cycle_combo.addItems(["한 번만", "연속 릴레이 (작업 완료 후 지정시간 휴식)", "지정 시간 간격 (무조건 해당 시간마다)"])
        self.cycle_combo.setCurrentIndex(1)
        
        from PyQt5.QtWidgets import QSpinBox
        self.time_spin = QSpinBox()
        self.time_spin.setRange(1, 1440)
        self.time_spin.setValue(15)
        self.time_label = QLabel("분")
        
        self.cycle_combo.currentTextChanged.connect(self.on_cycle_mode_changed)

        opt_layout.addWidget(self.cycle_combo)
        opt_layout.addWidget(self.time_spin)
        opt_layout.addWidget(self.time_label)
        opt_layout.addStretch()
        layout.addWidget(opt_group)

        self.btn_start = QPushButton("분석 및 서버 전송 시작")
        self.btn_start.setObjectName("primaryBtn")
        self.btn_start.setFixedHeight(55)
        self.btn_start.clicked.connect(self.toggle_process)
        layout.addWidget(self.btn_start)
        
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("logWindow")
        layout.addWidget(self.log_view)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.handle_timer_tick)
        self.is_active = False
        self.worker_stop_event = None

    def on_cycle_mode_changed(self, text):
        if text == "한 번만":
            self.time_spin.setEnabled(False)
        else:
            self.time_spin.setEnabled(True)

    def add_row(self, table_obj, data_list):
        row = table_obj.rowCount()
        table_obj.insertRow(row)
        for i, val in enumerate(data_list): table_obj.setItem(row, i, QTableWidgetItem(str(val)))

    def save_list(self):
        comp_data = []
        for i in range(self.table.rowCount()):
            id_item = self.table.item(i, 0)
            name_item = self.table.item(i, 1)
            if id_item and id_item.text().strip():
                comp_data.append({"id": id_item.text().strip(), "name": name_item.text().strip() if name_item else ""})
        try:
            with open(SWEEP_MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(comp_data, f, ensure_ascii=False, indent=4)
        except: pass

    def load_list(self):
        if os.path.exists(SWEEP_MEMORY_FILE):
            try:
                with open(SWEEP_MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data: self.add_row(self.table, [item['id'], item['name']])
            except: pass
        else:
            self.add_row(self.table, ["113409", "다산자이"])

    def schedule_after_sleep_window(self):
        wait_seconds = seconds_until_operating_start()
        wait_ms = wait_seconds * 1000
        self.log_view.append(f"\n🌙 현재는 휴식 시간(00:00~07:59)입니다. {format_elapsed(wait_seconds)} 후 08:00에 수집을 재개합니다.")
        self.timer.start(wait_ms)

    def schedule_next_cycle(self):
        wait_ms = self.time_spin.value() * 60 * 1000
        next_run = datetime.datetime.now() + datetime.timedelta(milliseconds=wait_ms)
        if not is_operating_time(next_run):
            wait_seconds = seconds_until_operating_start()
            self.log_view.append(f"\n🌙 다음 예정 시간이 휴식 시간입니다. {format_elapsed(wait_seconds)} 후 08:00에 다음 릴레이를 시작합니다.")
            self.timer.start(wait_seconds * 1000)
            return
        self.log_view.append(f"\n⏳ 작업 완료! {self.time_spin.value()}분 휴식 후 다음 릴레이 출발합니다...")
        self.timer.start(wait_ms)

    def toggle_process(self):
        if self.is_active:
            self.timer.stop()
            self.is_active = False
            if self.worker_stop_event:
                self.worker_stop_event.set()
            self.btn_start.setText("분석 및 서버 전송 시작")
            self.btn_start.setObjectName("primaryBtn")
            self.cycle_combo.setEnabled(True)
            self.time_spin.setEnabled(True)
            self.setStyleSheet(self.styleSheet())
            self.log_view.append("\n🛑 중지 요청을 보냈습니다. 진행 중인 요청/대기 지점에서 곧 멈춥니다.")
        else:
            self.save_list()
            if self.cycle_combo.currentText() == "한 번만":
                self.is_active = True
                self.cycle_combo.setEnabled(False)
                self.time_spin.setEnabled(False)
                self.btn_start.setText("수집 중지 (클릭 시 중지 요청)")
                self.btn_start.setObjectName("dangerBtn")
                self.setStyleSheet(self.styleSheet())
                self.execute_analysis()
            else:
                self.is_active = True
                self.cycle_combo.setEnabled(False)
                self.time_spin.setEnabled(False)
                self.btn_start.setText("자동 스캔 중지 (클릭 시 정지)")
                self.btn_start.setObjectName("dangerBtn")
                self.setStyleSheet(self.styleSheet())
                
                if "지정 시간" in self.cycle_combo.currentText():
                    ms = self.time_spin.value() * 60 * 1000
                    self.timer.start(ms)
                
                self.execute_analysis()

    def handle_timer_tick(self):
        if self.is_active:
            if not is_operating_time():
                self.schedule_after_sleep_window()
                return
            self.log_view.append(f"\n⏰ 설정된 시간({self.time_spin.value()}분)이 경과되어 스캔을 시작합니다.")
            if "릴레이" in self.cycle_combo.currentText():
                self.timer.stop() 
            self.execute_analysis()

    def on_worker_finished(self):
        if not self.is_active: 
            self.btn_start.setEnabled(True)
            self.worker_stop_event = None
            return

        self.worker_stop_event = None
            
        if "릴레이" in self.cycle_combo.currentText():
            self.schedule_next_cycle()
        else:
            self.is_active = False
            self.btn_start.setText("분석 및 서버 전송 시작")
            self.btn_start.setObjectName("primaryBtn")
            self.cycle_combo.setEnabled(True)
            self.time_spin.setEnabled(True)
            self.setStyleSheet(self.styleSheet())

    def execute_analysis(self):
        if not is_operating_time():
            self.schedule_after_sleep_window()
            return

        clist = []
        for i in range(self.table.rowCount()):
            id_txt = self.table.item(i, 0).text().strip() if self.table.item(i, 0) else ""
            nm_txt = self.table.item(i, 1).text().strip() if self.table.item(i, 1) else ""
            if id_txt: clist.append({"id": id_txt, "name": nm_txt})
            
        if not clist: return
        self.sigs = WorkerSignals(); self.sigs.log.connect(self.log_view.append)
        self.sigs.finished.connect(self.on_worker_finished)
        
        self.worker_stop_event = threading.Event()
        worker = MarketSweepWorker(clist, self.sigs, self.worker_stop_event)
        threading.Thread(target=worker.run, daemon=True).start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow(); window.show()
    sys.exit(app.exec_())