import os
import time
import random
import re
import socket
import pandas as pd
import threading
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc

# --- [PyQt5 GUI 라이브러리 추가] ---
try:
    import PyQt5
    plugin_path = os.path.join(os.path.dirname(PyQt5.__file__), 'Qt5', 'plugins', 'platforms')
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = plugin_path
except ImportError:
    pass

from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
from PyQt5.QtCore import pyqtSignal, QObject
import sys

# --- [수집 설정] ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CHECKPOINT_FILE = os.path.join(BASE_DIR, "last_complex_id.txt")
DATA_FILE = os.path.join(BASE_DIR, "Total_Realtor_Master_DB.xlsx")

MIN_HOUSEHOLDS = 300 
PRICE_MIN = 5        
PRICE_MAX = 50       

# --- [GUI 로그 전달용 시그널 클래스] ---
class WorkerSignals(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal()

class RealtorMasterCrawler:
    def __init__(self, signals=None):
        self.signals = signals
        self.is_running = True # 중단 제어용 플래그
        
        self.log("⚙️ 마스터 엔진 최적화 시작 (대량 수집 모드)...")
        self.driver = self.setup_driver()
        self.collected_phones = self.load_existing_phones()
        self.start_id = self.load_checkpoint()

    def wait_for_internet(self):
        """인터넷이 연결될 때까지 무한 대기합니다."""
        while self.is_running:
            try:
                socket.create_connection(("8.8.8.8", 53), timeout=3)
                return True
            except OSError:
                self.log("📡 인터넷 연결 끊김... 연결을 기다립니다.")
                time.sleep(30)
        return False

    def setup_driver(self):
        self.wait_for_internet()
        options = uc.ChromeOptions()
        options.add_argument("--window-size=1300,1000")
        options.add_argument('--disable-blink-features=AutomationControlled')
        
        try:
            # [수정 완료] 크롬 최신 버전에 맞춰 145 -> 147로 변경
            driver = uc.Chrome(options=options, version_main=147)
        except Exception as e:
            self.log(f"⚠️ 1차 로드 실패, 재시도 중... ({e})")
            self.wait_for_internet()
            # [수정 완료] 여기도 147로 변경
            driver = uc.Chrome(options=options, version_main=147, use_subprocess=True)
            
        driver.implicitly_wait(5)
        return driver

    def log(self, msg):
        log_str = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(log_str)
        if self.signals:
            self.signals.log.emit(log_str)

    def load_checkpoint(self):
        if os.path.exists(CHECKPOINT_FILE):
            with open(CHECKPOINT_FILE, "r") as f:
                return int(f.read().strip())
        return 1

    def save_checkpoint(self, c_id):
        with open(CHECKPOINT_FILE, "w") as f:
            f.write(str(c_id))

    def load_existing_phones(self):
        if os.path.exists(DATA_FILE):
            try:
                df = pd.read_excel(DATA_FILE)
                return set(df['핸드폰 번호'].astype(str).tolist())
            except: return set()
        return set()

    def parse_price(self, text):
        match = re.search(r'(\d+)억', text)
        return int(match.group(1)) if match else 0

    def run(self):
        self.log(f"🚀 작업 시작: ID {self.start_id}번부터 대량 분석합니다.")
        for c_id in range(self.start_id, 200001):
            if not self.is_running:
                self.log("🛑 중단 요청 확인. 안전하게 작업을 종료합니다.")
                break

            if 27894 <= c_id <= 100000: continue # 유령 구간 점프

            try:
                url = f"https://new.land.naver.com/complexes/{c_id}?a=APT:ABYG:JGC:PRE&b=A1&ad=true"
                
                try:
                    self.driver.get(url)
                except Exception as e:
                    if "ERR_NAME_NOT_RESOLVED" in str(e):
                        self.wait_for_internet()
                        self.driver.get(url)
                    else: raise e

                try:
                    WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".complex_title")))
                except:
                    self.save_checkpoint(c_id)
                    continue

                time.sleep(2)
                body_text = self.driver.find_element(By.TAG_NAME, "body").text
                
                hh_match = re.search(r'([\d,]+)세대', body_text)
                households = int(hh_match.group(1).replace(",", "")) if hh_match else 0
                price_match = re.search(r'최근\s?매매\s?실거래가\n?([\d억\s,]+)', body_text)
                price_val = self.parse_price(price_match.group(1)) if price_match else 0

                if households < MIN_HOUSEHOLDS or not (PRICE_MIN <= price_val <= PRICE_MAX):
                    self.save_checkpoint(c_id)
                    continue

                self.log(f"✅ [ID {c_id}] {households}세대 통과 -> 부동산 리스트 확장 중...")

                try:
                    scroll_pane = self.driver.find_element(By.CSS_SELECTOR, ".item_list.item_list--article")
                    for _ in range(6): 
                        self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", scroll_pane)
                        time.sleep(0.8)
                except: pass

                items = self.driver.find_elements(By.CSS_SELECTOR, ".item_list--article .item")[:100]
                self.log(f"   🔎 발견된 매물 {len(items)}개 분석 시작...")
                
                for item in items:
                    if not self.is_running: break
                    self.analyze_realtor(item, c_id, households, price_val)
                    time.sleep(random.uniform(0.7, 1.5))

            except Exception as e:
                self.log(f"❌ ID {c_id} 분석 중 오류: {e}")
                if "session" in str(e).lower():
                    try: self.driver.quit()
                    except: pass
                    self.driver = self.setup_driver()
            
            self.save_checkpoint(c_id)

        if self.driver:
            try: self.driver.quit()
            except: pass
        if self.signals:
            self.signals.finished.emit()

    def analyze_realtor(self, item, c_id, hh, price):
        try:
            multicp = item.find_elements(By.CSS_SELECTOR, ".label--multicp")
            if multicp:
                self.driver.execute_script("arguments[0].click();", multicp[0])
                time.sleep(0.5)

            title_btn = item.find_element(By.CSS_SELECTOR, ".item_title")
            self.driver.execute_script("arguments[0].click();", title_btn)
            time.sleep(1.5)

            detail_pane = WebDriverWait(self.driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".detail_contents")))
            detail_text = detail_pane.text
            
            phone_match = re.search(r'010[- .]?\d{3,4}[- .]?\d{4}', detail_text)
            phone = phone_match.group().replace("-", "").replace(" ", "").replace(".", "") if phone_match else None

            if not phone or phone in self.collected_phones:
                return

            lines = [l.strip() for l in detail_text.split('\n') if l.strip()]
            realtor_name, ceo, address, count_text, cp_name = "정보없음", "정보없음", "정보없음", "0", "정보없음"
            
            for i, line in enumerate(lines):
                if "대표" in line:
                    ceo = line.replace("대표", "").split("등록번호")[0].strip()
                    if i > 0: realtor_name = re.split(r'\(|등|길찾기|제공|톡톡문의', lines[i-1])[0].strip()
                    addr_lines = []
                    for j in range(i+1, len(lines)):
                        if any(k in lines[j] for k in ["전화", "010", "최근3개월", "매매", "전세"]): break
                        addr_lines.append(lines[j].replace("소재지", "").strip())
                    if addr_lines: address = " ".join(addr_lines).strip()
                if "매매" in line and "전세" in line: count_text = line.strip()

            try:
                cp_element = self.driver.find_element(By.CSS_SELECTOR, ".agent_cp_info")
                cp_name = cp_element.text.replace("제공", "").strip()
            except:
                for l in reversed(lines):
                    if "제공" in l and len(l) < 20:
                        cp_name = l.replace("제공", "").strip()
                        break

            data = {
                "수집일시": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "단지ID": c_id, "세대수": hh, "실거래가": f"{price}억대",
                "부동산명": realtor_name, "대표자명": ceo, "주소": address,
                "핸드폰 번호": phone, "매물수": count_text, "CP사": cp_name
            }

            self.save_to_excel(data)
            self.collected_phones.add(phone)
            self.log(f"      ✨ 신규 수집: {realtor_name} ({phone})")

        except: pass

    def save_to_excel(self, data):
        df_new = pd.DataFrame([data])
        if not os.path.exists(DATA_FILE):
            df_new.to_excel(DATA_FILE, index=False)
        else:
            try:
                df_old = pd.read_excel(DATA_FILE)
                df_total = pd.concat([df_old, df_new], ignore_index=True)
                df_total['핸드폰 번호'] = df_total['핸드폰 번호'].astype(str)
                df_total = df_total.drop_duplicates(subset=['핸드폰 번호'], keep='last')
                df_total.to_excel(DATA_FILE, index=False)
            except Exception as e:
                self.log(f"⚠️ 엑셀 저장 중 오류 발생: {e}")

# ======================================================
# [GUI] 메인 윈도우 (안전 종료 지원)
# ======================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("이실장 마스터 DB 수집기")
        self.setGeometry(100, 100, 550, 650)
        self.setStyleSheet("QWidget { font-family: 'Malgun Gothic'; background-color: #f8fafc; }")

        layout = QVBoxLayout()
        self.label = QLabel("🚀 마스터 DB 대량 수집 (안전 중단 지원)")
        self.label.setStyleSheet("font-size: 13px; color: #333d4b; margin-bottom: 10px;")
        layout.addWidget(self.label)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("background-color: #1e293b; color: #f8fafc; border-radius: 10px; font-family: 'Consolas'; font-size: 12px; line-height: 1.5;")
        layout.addWidget(self.log_view)

        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("엔진 시작")
        self.btn_start.setFixedHeight(50)
        self.btn_start.setStyleSheet("background-color: #3182f6; color: white; font-weight: bold; font-size: 14px; border-radius: 8px;")
        self.btn_start.clicked.connect(self.start_crawler)
        
        self.btn_stop = QPushButton("안전 중단 (저장 후 멈춤)")
        self.btn_stop.setFixedHeight(50)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold; font-size: 14px; border-radius: 8px;")
        self.btn_stop.clicked.connect(self.stop_crawler)

        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        layout.addLayout(btn_layout)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        
        self.worker = None

    def start_crawler(self):
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.log_view.append("⚙️ 백그라운드 엔진을 예열 중입니다...")

        self.signals = WorkerSignals()
        self.signals.log.connect(self.log_view.append)
        self.signals.finished.connect(self.on_finished)

        self.worker = RealtorMasterCrawler(signals=self.signals)
        threading.Thread(target=self.worker.run, daemon=True).start()

    def stop_crawler(self):
        if self.worker:
            self.worker.is_running = False
            self.btn_stop.setText("중단 처리 중...")
            self.btn_stop.setEnabled(False)

    def on_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setText("안전 중단 (저장 후 멈춤)")
        self.btn_stop.setEnabled(False)
        self.log_view.append("\n✅ 엔진 작동이 안전하게 종료되었습니다.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())