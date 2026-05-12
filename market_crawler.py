import os
import sys
import time
import threading
import datetime
import random
import re
import urllib.request
import urllib.error
import json
import base64
import subprocess
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- [상수 및 경로 설정] ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SWEEP_MEMORY_FILE = os.path.join(BASE_DIR, "my_sweep_list.json")
VIP_MEMORY_FILE = os.path.join(BASE_DIR, "my_vip_list.json")

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
                             QHeaderView, QTextEdit, QGroupBox, QComboBox, QCheckBox)
from PyQt5.QtCore import pyqtSignal, QObject, QTimer, Qt
from selenium.webdriver.common.by import By
import undetected_chromedriver as uc

try:
    import pandas as pd
except ImportError:
    pd = None

# ======================================================
# 🧹 [자원 관리] 브라우저 찌꺼기 강제 청소기
# ======================================================
def cleanup_chrome_processes():
    try:
        if sys.platform == "win32":
            subprocess.run("taskkill /F /IM chromedriver.exe /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run("taskkill /F /IM chrome.exe /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        pass

# ======================================================
# 🚀 깃허브 자동 업로드 엔진
# ======================================================
def auto_github_push(target_filename, log_func=None):
    token = "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"
    repo = "birossarts9/realestate-date-report"
    file_path = os.path.join(BASE_DIR, target_filename)
    url = f"https://api.github.com/repos/{repo}/contents/{target_filename}"

    try:
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

class WorkerSignals(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal()

# ======================================================
# [Worker] 시장 분석 엔진
# ======================================================
class MarketSweepWorker:
    def __init__(self, c_list, vip_list, stealth, keep_open, do_cleanup, sigs):
        self.c_list = c_list
        self.vip_list = vip_list
        self.stealth = stealth
        self.keep_open = keep_open
        self.do_cleanup = do_cleanup
        self.sigs = sigs
        
    def send_log(self, m): 
        self.sigs.log.emit(m)

    def run(self):
        self.send_log("📡 [마스터 스케줄러] 통제실 데이터를 불러옵니다...")
        self.control_map = load_control_room()
        
        # 🚨 [핵심 복원] 통제실에 있는 '부동산명'을 추출해 예전의 VIP 리스트로 자동 부활시킵니다!
        self.vip_agents = list(set([info['부동산명'] for info in self.control_map.values() if info['부동산명']]))

        if self.do_cleanup:
            cleanup_chrome_processes()
        
        self.send_log(f"🚀 [시장 분석] 스캔 시작 (감시 VIP: {', '.join(self.vip_agents)})")

        # ======================================================
        # 🧠 [스마트 캐시] 기존 데이터 로드 (클릭 80% 감소 목적)
        # ======================================================
        self.send_log("🧠 스마트 캐시 데이터를 뇌내 메모리에 적재 중...")
        cache_dict = {}
        # 🚨 [수정] 스마트 캐시도 초고속 파케이로 읽어오도록 업그레이드
        month_str_cache = datetime.datetime.now().strftime("%Y_%m")
        cache_parquet_fname = os.path.join(BASE_DIR, f"naver_market_report_{month_str_cache}.parquet")
        cache_excel_fname = os.path.join(BASE_DIR, f"naver_market_report_{month_str_cache}.xlsx")
        
        # 파케이 파일이 있으면 그걸 읽고, 없으면 엑셀을 읽도록 똑똑하게 분기 처리
        target_cache_file = cache_parquet_fname if os.path.exists(cache_parquet_fname) else cache_excel_fname
        
        if os.path.exists(target_cache_file) and pd is not None:
            try:
                if target_cache_file.endswith('.parquet'):
                    df_cache = pd.read_parquet(target_cache_file)
                else:
                    df_cache = pd.read_excel(target_cache_file)
                for _, row in df_cache.iterrows():
                    # 캐시 키: 단지명 + 부동산명 + 동/호수 + 층/타입 + 거래방식 + 가격
                    key = (str(row.get('단지명','')), str(row.get('부동산명','')), str(row.get('동/호수','')), str(row.get('층/타입','')), str(row.get('거래방식','')), str(row.get('가격','')))
                    cache_dict[key] = {
                        '고유번호': str(row.get('고유번호', '번호없음')),
                        '확인일자': str(row.get('확인일자', '날짜없음'))
                    }
                self.send_log(f"⚡ 캐시 장전 완료: {len(cache_dict)}개 매물 기억됨 (클릭 패스 준비 완료)")
            except Exception as e:
                self.send_log(f"⚠️ 캐시 로드 실패 (신규 수집으로 진행): {e}")

        options = uc.ChromeOptions()
        options.add_argument("--window-size=1920,1080")
        if self.stealth:
            options.add_argument("--window-position=-32000,-32000")
        
        driver = None
        try: 
            try: 
                driver = uc.Chrome(options=options, version_main=145)
            except: 
                driver = uc.Chrome(options=options)
                
            all_data = []
            TARGET_ANALYZE = 40
            
            for index, comp in enumerate(self.c_list):
                c_id, c_name = comp['id'], comp['name']
                self.send_log(f"\n🏢 [{index+1}/{len(self.c_list)}] {c_name} 분석 중...")
                
                driver.get(f"https://new.land.naver.com/complexes/{c_id}?ms=37.6,127.1,17&a=APT:ABYG:JGC:PRE&e=RETAIL")
                time.sleep(random.uniform(5.0, 7.0))
                
                try:
                    group_cb = driver.find_element(By.CSS_SELECTOR, "input[id*='address_group']")
                    if not group_cb.is_selected():
                        label = driver.find_element(By.CSS_SELECTOR, f"label[for='{group_cb.get_attribute('id')}']")
                        driver.execute_script("arguments[0].click();", label)
                        time.sleep(3.0)
                except: pass

                try:
                    container = driver.find_element(By.CSS_SELECTOR, ".item_list.item_list--article")
                    for _ in range(6):
                        driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
                        time.sleep(1.2)
                except: pass

                c_rank = 1
                cur_items = driver.find_elements(By.CSS_SELECTOR, ".item_list--article .item")

                for i in range(min(len(cur_items), TARGET_ANALYZE)):
                    try:
                        it = cur_items[i]
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", it)
                        
                        raw_text_main = driver.execute_script("return arguments[0].textContent;", it)
                        main_date_match = re.search(r"(\d{2}\.\d{2}\.\d{2})", raw_text_main)
                        fallback_date = main_date_match.group(1) if main_date_match else "날짜없음"
                        
                        try:
                            t_els = it.find_elements(By.CSS_SELECTOR, ".item_title .text") or it.find_elements(By.CSS_SELECTOR, ".item_title")
                            title = driver.execute_script("return arguments[0].textContent;", t_els[0]).strip() if t_els else ""
                        except:
                            title = "확인불가"

                        try:
                            p_els = it.find_elements(By.CSS_SELECTOR, ".price_line")
                            raw_price = driver.execute_script("return arguments[0].textContent;", p_els[0]).strip().replace('\n', ' ') if p_els else ""
                        except:
                            raw_price = ""

                        trade_type = ""
                        price_val = raw_price
                        for t_keyword in ["매매", "전세", "월세", "단기"]:
                            if raw_price.startswith(t_keyword):
                                trade_type = t_keyword
                                price_val = raw_price[len(t_keyword):].strip() 
                                break

                        try:
                            s_els = it.find_elements(By.CSS_SELECTOR, ".info_area .spec") or it.find_elements(By.CSS_SELECTOR, ".spec")
                            floor_spec = driver.execute_script("return arguments[0].textContent;", s_els[0]).strip() if s_els else ""
                        except:
                            floor_spec = ""

                        if not title or not raw_price: continue

                        now_dt = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        multicp_btns = it.find_elements(By.CSS_SELECTOR, ".label--multicp")

                        # ======================================================
                        # 🎯 [1] 묶음 매물 처리
                        # ======================================================
                        if multicp_btns:
                            driver.execute_script("arguments[0].click();", multicp_btns[0])
                            time.sleep(1.5)
                            
                            agent_elements = it.find_elements(By.CSS_SELECTOR, ".agent_name")
                            seen_agents = set()
                            idx = 1
                            
                            for a_el in agent_elements:
                                agent_name = driver.execute_script("return arguments[0].textContent;", a_el).strip()
                                if not agent_name or "제공" in agent_name or "네이버" in agent_name: continue
                                
                                if agent_name in seen_agents: continue
                                seen_agents.add(agent_name)

                                # 🚨 [수술 1단계] 화면 흔들리기 전, 기적의 코드(4칸 위)로 날짜부터 추출
                                try:
                                    js_get_parent_text = """
                                        var el = arguments[0];
                                        var curr = el;
                                        for(var j=0; j<4; j++) {
                                            if(curr.parentElement) curr = curr.parentElement;
                                        }
                                        return curr.textContent;
                                    """
                                    parent_text = driver.execute_script(js_get_parent_text, a_el)
                                    date_match = re.search(r"(\d{2}\.\d{2}\.\d{2})", parent_text)
                                    confirm_date = date_match.group(1) if date_match else fallback_date
                                except:
                                    confirm_date = fallback_date

                                # 🚨 [스마트 캐시 적용] 클릭 전 장부 확인
                                cache_key = (c_name, agent_name, title, floor_spec, trade_type, price_val)
                                
                                if cache_key in cache_dict and cache_dict[cache_key]['고유번호'] != "번호없음" and cache_dict[cache_key]['확인일자'] == confirm_date:
                                    article_no = cache_dict[cache_key]['고유번호']
                                else:
                                    try:
                                        driver.execute_script("""
                                            var agent_click = arguments[0];
                                            var row_click = agent_click.closest('li') || agent_click.closest('.item_inner') || agent_click.parentNode.parentNode;
                                            var safeTarget = row_click.querySelector('.price_line') || row_click.querySelector('.item_link') || row_click;
                                            safeTarget.click();
                                        """, a_el)
                                        time.sleep(0.6)
                                        current_url = driver.current_url
                                        no_match = re.search(r'articleNo=(\d+)', current_url)
                                        article_no = no_match.group(1) if no_match else "번호없음"
                                        
                                        cache_dict[cache_key] = {'고유번호': article_no, '확인일자': confirm_date}
                                    except:
                                        article_no = "번호없음"
                                        
                                all_data.append({
                                    "매물번호": f"#{c_rank:03d}", "수집일시": now_dt, "단지명": c_name, 
                                    "전체순위": c_rank, "노출형태": "묶음", "묶음내순위": idx, 
                                    "부동산명": agent_name, "동/호수": title, "거래방식": trade_type, 
                                    "가격": price_val, "층/타입": floor_spec, 
                                    "확인일자": confirm_date,
                                    "고유번호": article_no
                                })
                                
                                # ==========================================
                                # 🧠 1. [순위로그] VIP 부동산 전체 매물 기록 (예전 기능 복원)
                                # ==========================================
                                if any(vip in agent_name for vip in self.vip_agents):
                                    self.send_log(f"📊 [VIP 매물 수집] {agent_name} - {title} ({c_rank}위)")
                                    append_to_sheet("순위로그", [now_dt, c_name, agent_name, c_rank, idx, title, price_val, floor_spec, article_no])
                                
                                # ==========================================
                                # 🧠 2. [작업지시서] 통제실 타겟 매물 방어 로직 (스펙 추적 기반)
                                # ==========================================
                                # 방금 스캔한 화면의 정보로 스펙(지문) 키를 실시간 조립
                                current_spec_key = f"{c_name.strip()}|{title.strip()}|{floor_spec.strip()}|{trade_type.strip()}|{price_val.strip()}"
                                
                                # 통제실에 등록된 스펙과 일치하는 내 매물인지 확인
                                if current_spec_key in self.control_map:
                                    target_info = self.control_map[current_spec_key]
                                    
                                    if target_info['부동산명'] in agent_name:
                                        my_rank = c_rank  # 전체 순위를 기준으로 방어
                        
                                        if my_rank > target_info['방어마지노선']:
                                            last_update_str = target_info['최근갱신일시']
                                            cooldown_hours = target_info['쿨타임']
                                            is_cooldown_passed = True
                        
                                            if last_update_str:
                                                try:
                                                    last_dt = datetime.datetime.strptime(last_update_str, "%Y-%m-%d %H:%M:%S")
                                                    diff_hours = (datetime.datetime.now() - last_dt).total_seconds() / 3600
                                                    if diff_hours < cooldown_hours:
                                                        is_cooldown_passed = False
                                                except: pass
                        
                                            if is_cooldown_passed:
                                                self.send_log(f"🚨 [작업지시 발동] {title} 마지노선 이탈! (새 매물번호: {article_no})")
                                                # 💡 가장 최신의 article_no를 따서 작업지시서에 꽂아 넣습니다!
                                                append_to_sheet("작업지시서", [now_dt, target_info['부동산명'], target_info['아이디'], target_info['비밀번호'], current_spec_key, article_no, "대기"])
                                                self.control_map[current_spec_key]['최근갱신일시'] = now_dt
                                            else:
                                                self.send_log(f"⏳ [대기] {title} 매물이 밀려났으나 아직 쿨타임 남음.")
                                        else:
                                            self.send_log(f"🛡️ [방어 성공] {title} 타겟 매물은 안전권입니다.")
                                        
                                idx += 1
                                
                            driver.execute_script("arguments[0].click();", multicp_btns[0])
                            time.sleep(random.uniform(0.3, 0.5))
                            c_rank += 1

                        # ======================================================
                        # ⚡ [2] 단독 매물 처리
                        # ======================================================
                        else:
                            confirm_date = fallback_date
                            raw_ags = [driver.execute_script("return arguments[0].textContent;", a).strip() for a in it.find_elements(By.CSS_SELECTOR, ".agent_name")]
                            real_ags = list(dict.fromkeys([a for a in raw_ags if a and "제공" not in a and "네이버" not in a]))
                            agent_name = real_ags[0] if real_ags else "파악불가"

                            try:
                                driver.execute_script("""
                                    var safeTarget = arguments[0].querySelector('.price_line') || arguments[0].querySelector('.item_link') || arguments[0];
                                    safeTarget.click();
                                """, it)
                                time.sleep(0.6)
                                current_url = driver.current_url
                                no_match = re.search(r'articleNo=(\d+)', current_url)
                                article_no = no_match.group(1) if no_match else "번호없음"
                            except:
                                article_no = "번호없음"

                            row = {
                                "매물번호": f"#{c_rank:03d}", "수집일시": now_dt, "단지명": c_name,
                                "전체순위": c_rank, "노출형태": "단독", "묶음내순위": 1,
                                "부동산명": agent_name, "동/호수": title, "거래방식": trade_type,
                                "가격": price_val, "층/타입": floor_spec, "확인일자": confirm_date, "고유번호": article_no
                            }
                            all_data.append(row)
                            
                            # ==========================================
                            # 🧠 1. [순위로그] VIP 부동산 전체 매물 기록 (예전 기능 복원)
                            # ==========================================
                            if any(vip in agent_name for vip in self.vip_agents):
                                self.send_log(f"📊 [VIP 매물 수집] {agent_name} - {title} ({c_rank}위)")
                                append_to_sheet("순위로그", [now_dt, c_name, agent_name, c_rank, 1, title, price_val, floor_spec, article_no])
                            
                            # ==========================================
                            # 🧠 2. [작업지시서] 통제실 타겟 매물 방어 로직 (스펙 추적 기반)
                            # ==========================================
                            current_spec_key = f"{c_name.strip()}|{title.strip()}|{floor_spec.strip()}|{trade_type.strip()}|{price_val.strip()}"
                            
                            if current_spec_key in self.control_map:
                                target_info = self.control_map[current_spec_key]
                                
                                if target_info['부동산명'] in agent_name:
                                    my_rank = c_rank 
                    
                                    if my_rank > target_info['방어마지노선']:
                                        last_update_str = target_info['최근갱신일시']
                                        cooldown_hours = target_info['쿨타임']
                                        is_cooldown_passed = True
                    
                                        if last_update_str:
                                            try:
                                                last_dt = datetime.datetime.strptime(last_update_str, "%Y-%m-%d %H:%M:%S")
                                                diff_hours = (datetime.datetime.now() - last_dt).total_seconds() / 3600
                                                if diff_hours < cooldown_hours:
                                                    is_cooldown_passed = False
                                            except: pass
                    
                                        if is_cooldown_passed:
                                            self.send_log(f"🚨 [작업지시 발동] {title} 마지노선 이탈! (새 매물번호: {article_no})")
                                            append_to_sheet("작업지시서", [now_dt, target_info['부동산명'], target_info['아이디'], target_info['비밀번호'], current_spec_key, article_no, "대기"])
                                            self.control_map[current_spec_key]['최근갱신일시'] = now_dt
                                        else:
                                            self.send_log(f"⏳ [대기] {title} 매물이 밀려났으나 아직 쿨타임 남음.")
                                    else:
                                        self.send_log(f"🛡️ [방어 성공] {title} 타겟 매물은 안전권입니다.")
                                    
                            c_rank += 1
                    except Exception as e: 
                        continue

                # 휴식 로직
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
                    time.sleep(total_sleep)

            # 🚨 엑셀 저장 및 깃허브 전송
            if all_data and pd is not None:
                df_new = pd.DataFrame(all_data)
                
                col_order = ["매물번호", "수집일시", "단지명", "전체순위", "노출형태", "묶음내순위", "부동산명", "동/호수", "거래방식", "가격", "층/타입", "확인일자", "고유번호"]
                for col in col_order:
                    if col not in df_new.columns: df_new[col] = ""
                df_new = df_new[col_order]

                # 💡 [핵심 해결] 파케이 타입 충돌을 막기 위해 새 데이터를 모두 문자열(str)로 강제 변환!
                df_new = df_new.astype(str)

                # 🚨 [수정] 엑셀과 파케이(Parquet) 파일명을 둘 다 세팅
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
                            for col in col_order:
                                if col not in df_old.columns: df_old[col] = ""
                            df_old = df_old[col_order]
                            
                            # 💡 [핵심 해결] 기존에 불러온 엑셀 데이터도 모두 문자열(str)로 강제 변환!
                            df_old = df_old.astype(str)
                            
                            df_comb = pd.concat([df_old, df_new], ignore_index=True).drop_duplicates()
                            df_comb.to_excel(fname, index=False)
                            df_comb.to_parquet(parquet_fname, index=False)
                        else: 
                            df_new.to_excel(fname, index=False)
                            df_new.to_parquet(parquet_fname, index=False)
                        
                        save_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.send_log(f"\n💾 [{save_time_str}] 수집 및 엑셀/파케이 저장 완료! (초고속 압축 성공)")
                        
                        self.send_log("⏳ 깃허브 서버로 데이터 자동 전송 중...")
                        auto_github_push(target_filename, self.send_log)
                        auto_github_push(target_parquet_filename, self.send_log)
                        break
                    except Exception as e:
                        self.send_log(f"   ⚠️ 엑셀/파케이 저장 실패 (재시도 {attempt+1}/{max_retries}) - 에러 원인: {e}")
                        time.sleep(4.0)

        except Exception as e:
            self.send_log(f"❌ 전체 분석 오류: {e}")
        finally:
            if driver:
                if not self.keep_open:
                    try: 
                        driver.quit() 
                        self.send_log("🧹 브라우저 자원을 정상적으로 반환했습니다.")
                    except: pass
            self.sigs.finished.emit()


# ======================================================
# [GUI] 메인 윈도우 (릴레이 스케줄러 탑재)
# ======================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("이실장 Pro 시장 분석기 (V8.5 - 궁극 완성판)")
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

        self.stealth_chk = QCheckBox("화면 숨김(Stealth)")
        self.keep_chk = QCheckBox("창 유지(Debug)")
        self.clean_chk = QCheckBox("프로세스 정리")
        self.clean_chk.setChecked(False)
        
        opt_layout.addWidget(self.cycle_combo)
        opt_layout.addWidget(self.time_spin)
        opt_layout.addWidget(self.time_label)
        opt_layout.addStretch()
        opt_layout.addWidget(self.stealth_chk)
        opt_layout.addWidget(self.keep_chk)
        opt_layout.addWidget(self.clean_chk)
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

    def toggle_process(self):
        if self.is_active:
            self.timer.stop(); self.is_active = False
            self.btn_start.setText("분석 및 서버 전송 시작")
            self.btn_start.setObjectName("primaryBtn")
            self.cycle_combo.setEnabled(True)
            self.time_spin.setEnabled(True)
            self.setStyleSheet(self.styleSheet())
            self.log_view.append("\n🛑 자동 수집 스케줄이 중지되었습니다.")
        else:
            self.save_list()
            if self.cycle_combo.currentText() == "한 번만":
                self.btn_start.setEnabled(False)
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
            self.log_view.append(f"\n⏰ 설정된 시간({self.time_spin.value()}분)이 경과되어 스캔을 시작합니다.")
            if "릴레이" in self.cycle_combo.currentText():
                self.timer.stop() 
            self.execute_analysis()

    def on_worker_finished(self):
        if not self.is_active: 
            self.btn_start.setEnabled(True)
            return
            
        if "릴레이" in self.cycle_combo.currentText():
            wait_ms = self.time_spin.value() * 60 * 1000
            self.log_view.append(f"\n⏳ 작업 완료! {self.time_spin.value()}분 휴식 후 다음 릴레이 출발합니다...")
            self.timer.start(wait_ms)

    def execute_analysis(self):
        clist = []
        for i in range(self.table.rowCount()):
            id_txt = self.table.item(i, 0).text().strip() if self.table.item(i, 0) else ""
            nm_txt = self.table.item(i, 1).text().strip() if self.table.item(i, 1) else ""
            if id_txt: clist.append({"id": id_txt, "name": nm_txt})
            
        if not clist: return
        self.sigs = WorkerSignals(); self.sigs.log.connect(self.log_view.append)
        self.sigs.finished.connect(self.on_worker_finished)
        
        # 🚨 [수정 포인트] 예전의 vlist 대신 빈 리스트 [] 를 직접 넣습니다.
        worker = MarketSweepWorker(clist, [], self.stealth_chk.isChecked(), self.keep_chk.isChecked(), self.clean_chk.isChecked(), self.sigs)
        threading.Thread(target=worker.run, daemon=True).start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow(); window.show()
    sys.exit(app.exec_())