import sys
import os
import time
import threading
import datetime
import json
import re
import random
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QTextEdit, QGroupBox, QMessageBox)
from PyQt5.QtCore import pyqtSignal, QObject

print("--- [v2.4.1] API 로딩 완벽 대기(Async) 패치 기동 ---")

try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
except ImportError as e:
    print(f"🚨 라이브러리 로드 실패: {e}")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, 'frozen', False) else os.path.dirname(sys.executable)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SHEET_ID = "1yEllJWWNwsd5FMvvgwSIvA46j10XU_8MxpRAWcs-ba8"

class Logger(QObject):
    log_signal = pyqtSignal(str)
    ui_update_signal = pyqtSignal(str, str, str)

class RealEstateBot(QWidget):
    def __init__(self):
        super().__init__()
        self.driver = None
        self.logger = Logger()
        self.property_map = {} 
        self.spec_map = {}     # 추가: 매물스펙 임시 저장소
        self.company_map = {}  # 추가: 부동산명 임시 저장소
        self.init_ui()
        self.load_config()

    def init_ui(self):
        main_layout = QVBoxLayout()
        config_group = QGroupBox("수동 개별 실행 설정 (테스트용)")
        config_layout = QVBoxLayout()

        self.btn_load_sheet = QPushButton("📥 [작업지시서] 탭에서 맨 윗줄 불러오기 (수동 테스트용)")
        self.btn_load_sheet.setFixedHeight(35)
        self.btn_load_sheet.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
        self.btn_load_sheet.clicked.connect(self.run_load_sheet_task)
        config_layout.addWidget(self.btn_load_sheet)

        acc_layout = QHBoxLayout()
        self.id_input = QLineEdit(); self.pw_input = QLineEdit()
        acc_layout.addWidget(QLabel("계정:")); acc_layout.addWidget(self.id_input); acc_layout.addWidget(self.pw_input)
        config_layout.addLayout(acc_layout)

        config_layout.addWidget(QLabel("광고 매물 리스트 (10자리 번호 입력):"))
        self.prop_batch_input = QTextEdit()
        self.prop_batch_input.setFixedHeight(60)
        config_layout.addWidget(self.prop_batch_input)
        config_group.setLayout(config_layout)
        main_layout.addWidget(config_group)

        btn_layout = QHBoxLayout()
        self.btn_run_all = QPushButton("🚀 1. 수동 로그인 및 조회")
        self.btn_run_all.setFixedHeight(50); self.btn_run_all.setStyleSheet("background-color: #3182f6; color: white; font-weight: bold;")
        self.btn_run_all.clicked.connect(self.run_integrated_task)
        
        self.btn_execute = QPushButton("🔥 2. 실전 수동 재광고 (상태변경X)")
        self.btn_execute.setFixedHeight(50); self.btn_execute.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
        self.btn_execute.clicked.connect(self.confirm_and_run_execution)
        
        btn_layout.addWidget(self.btn_run_all); btn_layout.addWidget(self.btn_execute)
        main_layout.addLayout(btn_layout)

        self.btn_run_multi = QPushButton("♾️ 3. [작업지시서] 스마트 그룹핑 자동 릴레이 (상태변경O)")
        self.btn_run_multi.setFixedHeight(60)
        self.btn_run_multi.setStyleSheet("background-color: #8b5cf6; color: white; font-weight: bold; font-size: 15px; margin-top: 10px;")
        self.btn_run_multi.clicked.connect(self.run_multi_account_task)
        main_layout.addWidget(self.btn_run_multi)

        self.log_display = QTextEdit(); self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet("background-color: #0f172a; color: #38bdf8; font-family: 'Consolas'; font-size: 13px;")
        main_layout.addWidget(QLabel("실행 로그:")); main_layout.addWidget(self.log_display)

        self.setLayout(main_layout); self.setWindowTitle('부동산 광고 자동화 봇 v2.4.1 (로딩 대기 패치)')
        self.setGeometry(300, 300, 650, 800)
        self.logger.log_signal.connect(self.update_log)
        self.logger.ui_update_signal.connect(self.update_inputs_from_sheet)
        self.show()

    def update_inputs_from_sheet(self, u_id, u_pw, props):
        self.id_input.setText(u_id); self.pw_input.setText(u_pw); self.prop_batch_input.setPlainText(props)

    def load_config(self):
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f); self.id_input.setText(data.get("id", "")); self.pw_input.setText(data.get("pw", "")); self.prop_batch_input.setPlainText(data.get("props", ""))

    def save_config(self):
        with open(CONFIG_PATH, "w") as f: json.dump({"id": self.id_input.text(), "pw": self.pw_input.text(), "props": self.prop_batch_input.toPlainText()}, f)

    def update_log(self, text): self.log_display.append(f"[{time.strftime('%H:%M:%S')}] {text}")

    def get_gspread_client(self):
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        key_path = os.path.join(BASE_DIR, "service_account_key.json")
        creds = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
        return gspread.authorize(creds)

    def run_load_sheet_task(self): 
        threading.Thread(target=self.load_sheet_process, daemon=True).start()

    def load_sheet_process(self):
        self.logger.log_signal.emit("📥 [작업지시서] 탭에서 데이터를 불러옵니다...")
        try:
            client = self.get_gspread_client()
            sheet = client.open_by_key(SHEET_ID).worksheet("작업지시서")
            row_values = sheet.row_values(2)
            if len(row_values) >= 6:
                # F열(인덱스 5)에서 매물번호 로드
                self.logger.ui_update_signal.emit(row_values[2], row_values[3], row_values[5])
                self.logger.log_signal.emit(f"✅ [{row_values[1]}] 데이터 로드 완료!")
        except Exception as e: self.logger.log_signal.emit(f"🚨 시트 로드 에러: {e}")

    def log_to_sheets(self, p_id, status, note=""):
        try:
            client = self.get_gspread_client()
            sheet = client.open_by_key(SHEET_ID).worksheet("실행로그") 
            # 저장해둔 부동산명과 매물스펙을 불러와 6개 항목으로 구성
            comp = self.company_map.get(str(p_id), "")
            spec = self.spec_map.get(str(p_id), "")
            sheet.append_row([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), comp, spec, str(p_id), status, note])
        except Exception as e: 
            self.logger.log_signal.emit(f"🚨 실행로그 작성 실패: {e}")

    def accept_alert_if_present(self, wait_time=3):
        try:
            WebDriverWait(self.driver, wait_time).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            msg = alert.text
            alert.accept()
            return msg
        except TimeoutException:
            return None

    def run_integrated_task(self): 
        self.save_config(); self.property_map = {} 
        threading.Thread(target=self.integrated_process, daemon=True).start()

    def integrated_process(self):
        u_id = self.id_input.text(); u_pw = self.pw_input.text(); prop_list = re.findall(r'\d+', self.prop_batch_input.toPlainText())
        if not u_id or not u_pw or not prop_list: return
        self._login_and_fetch(u_id, u_pw, prop_list)

    def _login_and_fetch(self, u_id, u_pw, prop_list, sheet_obj=None, row_idx_map=None):
        try:
            if not self.driver:
                options = uc.ChromeOptions()
                options.add_argument(f'--user-data-dir={os.path.join(BASE_DIR, f"profile_{u_id}")}')
                self.driver = uc.Chrome(options=options, use_subprocess=True, version_main=146)
                
                self.driver.set_script_timeout(10)
                
                self.driver.get("https://www.aipartner.com/login")
                wait = WebDriverWait(self.driver, 20); wait.until(EC.presence_of_element_located((By.ID, "member-id")))
                self.driver.execute_script(f"document.getElementById('member-id').value='{u_id}'; document.getElementById('member-pw').value='{u_pw}'; document.getElementById('member-id').dispatchEvent(new Event('input', {{bubbles:true}})); document.getElementById('member-pw').dispatchEvent(new Event('input', {{bubbles:true}})); setTimeout(()=>document.querySelector('.btn-login').click(), 500);")
                time.sleep(5)
            
            for p_id in prop_list:
                self.logger.log_signal.emit(f"🔍 {p_id} 데이터 로딩 대기 중 (완전히 끝날 때까지 대기)...")
                
                script = f"""
                var callback = arguments[arguments.length - 1];
                fetch('/api/web/offerings/getOfferingsInfo?naverOfferingsSeq={p_id}', {{
                    headers: {{'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content}}
                }})
                .then(res => res.json())
                .then(data => callback(data))
                .catch(e => callback({{code: -1, message: String(e)}}));
                """
                
                res = self.driver.execute_async_script(script)
                
                if res and res.get('code') == 0:
                    self.property_map[p_id] = res['data']
                    self.logger.log_signal.emit(f"✅ {p_id}: 준비 완료")
                else:
                    err_msg = res.get('message', '서버 거부 (사유 불명)') if res else '응답 없음'
                    self.logger.log_signal.emit(f"⚠️ [{p_id}] 조회 실패 (사유: {err_msg}) - 건너뜁니다.")
                    self.log_to_sheets(p_id, "조회 실패", err_msg)
                    
                    if sheet_obj and row_idx_map:
                        sheet_row = row_idx_map.get((u_id, p_id))
                        if sheet_row:
                            sheet_obj.update_cell(sheet_row, 7, "조회실패") # 상태 열(G열) 6 -> 7
                            
                time.sleep(1)
                
            prepared_cnt = len(self.property_map)
            self.logger.log_signal.emit(f"✨ 총 {prepared_cnt}건 갱신 준비 완료.")
        except Exception as e: self.logger.log_signal.emit(f"🚨 조회 오류: {e}")

    def confirm_and_run_execution(self):
        if len(self.property_map) == 0: return
        reply = QMessageBox.question(self, '실전 전송 확인', f"현재 {len(self.property_map)}개의 매물을 재광고하시겠습니까?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes: threading.Thread(target=self.execute_ad_process, daemon=True).start()

    def execute_ad_process(self, sheet_obj=None, row_idx_map=None, current_uid=None):
        if not self.property_map:
            return
            
        self.logger.log_signal.emit(f"🚀 실전 광고 갱신 시작 (총 {len(self.property_map)}건)")
        
        for p_id, data in self.property_map.items():
            try:
                # [1] 시작 전 팝업 청소
                self.accept_alert_if_present(1) 

                self.logger.log_signal.emit(f"🌐 [{p_id}] 매물 검색 시작...")
                self.driver.get("https://www.aipartner.com/offerings/ad_list")
                time.sleep(2)
                
                # [2] 검색창 비우고 매물번호 입력 (잔상 제거)
                self.driver.execute_script(f"""
                    let input = document.getElementById('seq');
                    input.value = ''; 
                    input.value = '{p_id}';
                """)
                time.sleep(0.5)
                self.driver.execute_script("document.querySelector('.btnSearch').click();")
                
                # [3] ★ 핵심: 검색 결과가 '지금 매물'로 바뀔 때까지 대기
                # 단순히 버튼이 뜨는게 아니라, 데이터 속성이 p_id와 일치하는지 확인
                target_seq = data.get('offeringsSeq')
                selector = f"a#doubleRocketReAd[data-offerings=\"{target_seq}\"]"
                
                try:
                    # 해당 매물번호를 가진 버튼이 나타날 때까지 최대 10초 대기
                    WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                except:
                    # 만약 못 찾으면 리스트를 새로고침해서 한 번 더 시도
                    self.logger.log_signal.emit(f"⏳ [{p_id}] 검색 결과 지연... 새로고침 후 재시도")
                    self.driver.refresh()
                    time.sleep(2)
                    continue 

                # [4] 클릭 직전 버튼 재검증 (엉뚱한 매물 클릭 방지)
                self.logger.log_signal.emit(f"👆 [{p_id}] '재광고' 버튼 클릭 시도")
                click_script = f"""
                    let btn = document.querySelector('{selector}');
                    if(btn && btn.getAttribute('data-offerings') == '{target_seq}') {{
                        btn.click();
                        return true;
                    }}
                    return false;
                """
                if not self.driver.execute_script(click_script):
                    raise Exception("타겟 매물 버튼 매칭 실패")

                time.sleep(2)
                
                # [5] 바로 재광고 선택 및 전송
                self.driver.execute_script(f"document.querySelector('div.naverReAd[data-offeringsseq=\"{target_seq}\"] label').click();")
                time.sleep(1)
                
                # 체크박스 및 전송 (null 방지)
                self.driver.execute_script("let chk = document.getElementById('popAdEndCheck'); if(chk) chk.click();")
                self.driver.execute_script("let btn = document.querySelector('button.register.startReAdOfferings'); if(btn) btn.click();")
                
                # 알림창 두 번 걷어내기
                self.accept_alert_if_present(3)
                self.accept_alert_if_present(3)
                
                # [6] 결제 (setTimeout 제거 버전)
                self.logger.log_signal.emit(f"💳 [{p_id}] 결제 진행 중...")
                time.sleep(3) 
                
                payment_script = """
                    document.querySelectorAll('input[type="checkbox"]').forEach(cb => { if(!cb.checked) cb.click(); });
                    let payBtn = Array.from(document.querySelectorAll('a, button')).find(el => el.textContent.includes('결제하기'));
                    if(payBtn) { payBtn.click(); return true; }
                    return false;
                """
                if self.driver.execute_script(payment_script):
                    time.sleep(3)
                    final_msg = self.accept_alert_if_present(10)

                    # 성공 판정
                    if final_msg and any(kw in final_msg for kw in ["성공", "완료", "되었습니다"]):
                        self.logger.log_signal.emit(f"⭐ [{p_id}] 최종 성공")
                        self.log_to_sheets(p_id, "갱신 성공", final_msg)
                        if sheet_obj and row_idx_map and current_uid:
                            row = row_idx_map.get((current_uid, p_id))
                            if row: sheet_obj.update_cell(row, 7, "완료")
                    else:
                        raise Exception(f"결과 확인 불가: {final_msg}")
                else:
                    raise Exception("결제 버튼 클릭 실패")

            except Exception as e:
                self.logger.log_signal.emit(f"❌ [{p_id}] 에러: {str(e)}")
                # 에러 발생 시 시트에 실패 기록 후 '다음 매물'로 강제 이동
                self.log_to_sheets(p_id, "갱신 실패", str(e))
                if sheet_obj and row_idx_map and current_uid:
                    row = row_idx_map.get((current_uid, p_id))
                    if row: sheet_obj.update_cell(row, 7, "실패")
                continue # 루프가 멈추지 않게 다음 매물로!
                
        self.logger.log_signal.emit("🏁 현재 계정 작업 완료.")

    def run_multi_account_task(self):
        reply = QMessageBox.question(self, '스마트 릴레이 실행', "[작업지시서] 탭의 대기 중인 모든 매물을 업체별로 묶어서 연속 처리하시겠습니까?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            threading.Thread(target=self.smart_relay_process, daemon=True).start()

    def smart_relay_process(self):
        self.logger.log_signal.emit("♾️ 스마트 그룹핑 릴레이 모드를 시작합니다.")
        try:
            client = self.get_gspread_client()
            sheet = client.open_by_key(SHEET_ID).worksheet("작업지시서")
            all_records = sheet.get_all_values()
            
            tasks_by_account = {}
            row_idx_map = {} 
            latest_tasks = {} # 중복 제거용 딕셔너리
            
            # 1. 시트를 읽으면서 중복 매물 필터링 (가장 최신(마지막) 데이터만 남김)
            for idx, row in enumerate(all_records):
                if idx == 0 or len(row) < 7: continue 
                
                status = row[6].strip() # G열(상태)
                if status == "대기":
                    p_id = row[5].strip() # F열(매물번호)
                    # 동일한 p_id가 등장하면 자연스럽게 가장 마지막 row로 덮어씌워짐
                    latest_tasks[p_id] = (idx, row)
            
            # 2. 필터링된 데이터로 계정별 묶음(Grouping) 생성
            for idx, row in latest_tasks.values():
                comp_name = row[1].strip()
                u_id = row[2].strip()
                u_pw = row[3].strip()
                p_spec = row[4].strip() # E열(매물스펙)
                p_id = row[5].strip()   # F열(매물번호)
                
                acc_key = (comp_name, u_id, u_pw)
                
                if acc_key not in tasks_by_account:
                    tasks_by_account[acc_key] = []
                tasks_by_account[acc_key].append(p_id)
                row_idx_map[(u_id, p_id)] = idx + 1 
                
                # 매물 정보를 임시 저장하여 로그 남길 때 사용
                self.company_map[str(p_id)] = comp_name
                self.spec_map[str(p_id)] = p_spec
                    
            if not tasks_by_account:
                self.logger.log_signal.emit("✅ 현재 [작업지시서] 탭에 대기 중인 매물이 없습니다.")
                return

            for (company, u_id, u_pw), prop_list in tasks_by_account.items():
                self.logger.log_signal.emit(f"==================================================")
                self.logger.log_signal.emit(f"🚀 [{company}] 작업 시작 (묶음 대기 매물: {len(prop_list)}개)")
                
                props_str = ", ".join(prop_list)
                self.logger.ui_update_signal.emit(u_id, "***", props_str)
                self.property_map = {} 
                
                if self.driver:
                    self.logger.log_signal.emit("🧹 브라우저 세션 초기화 중...")
                    self.driver.quit()
                    time.sleep(2)
                    self.driver = None
                
                self._login_and_fetch(u_id, u_pw, prop_list, sheet_obj=sheet, row_idx_map=row_idx_map)
                
                if self.property_map:
                    self.execute_ad_process(sheet_obj=sheet, row_idx_map=row_idx_map, current_uid=u_id)
                
                self.logger.log_signal.emit(f"🏁 [{company}] 작업 완료. 3초 대기 후 다음 업체로 이동합니다.")
                time.sleep(3)
                
            self.logger.log_signal.emit("🎉 [작업지시서]에 쌓인 모든 릴레이 작업이 완벽하게 끝났습니다!")
            if self.driver:
                self.driver.quit()
                self.driver = None

        except Exception as e:
            self.logger.log_signal.emit(f"🚨 스마트 릴레이 작업 에러: {e}")

if __name__ == '__main__':
    app = QApplication(sys.argv); ex = RealEstateBot(); sys.exit(app.exec_())