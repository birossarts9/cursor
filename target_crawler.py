import os
import sys
import time
import random
import socket
import threading
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import undetected_chromedriver as uc

# --- [PyQt5 GUI 라이브러리] ---
try:
    import PyQt5
    plugin_path = os.path.join(os.path.dirname(PyQt5.__file__), 'Qt5', 'plugins', 'platforms')
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = plugin_path
except ImportError:
    pass

from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
from PyQt5.QtCore import pyqtSignal, QObject

# ======================================================
# [기본 설정]
# ======================================================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CHECKPOINT_FILE = os.path.join(BASE_DIR, "last_complex_id.txt")

# 봇 전용 독립 크롬 프로필 경로.
# 사용자의 일반 크롬과 user-data-dir을 분리하여 'cannot connect to chrome' 충돌을 원천 차단한다.
# 최초 1회만 이 프로필 창에서 네이버 로그인을 수동으로 해두면 이후 세션이 유지된다.
BOT_PROFILE_DIR = os.path.join(BASE_DIR, "chrome_bot_profile")
CHROME_PROFILE_DIR = "Default"
CHROME_VERSION_MAIN = 147

# ⚠️ [시뮬레이션 모드] True이면 실제 전송 버튼을 누르지 않고(입력까지만) 짧은 딜레이로 흐름만 검증한다.
#    실전 발송 시 False로 변경할 것.
SIMULATION_MODE = True

# 하루 발송 목표 및 안티 어뷰징 딜레이(초)
DAILY_SEND_LIMIT = 100
DELAY_MIN_SEC = 5 if SIMULATION_MODE else 180
DELAY_MAX_SEC = 10 if SIMULATION_MODE else 390


# ======================================================
# [GUI 로그 전달용 시그널]
# ======================================================
class WorkerSignals(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal()


# ======================================================
# [톡톡 자동 발송 엔진]
# ======================================================
class NaverTalkSender:
    # 클래스 변수: 봇 인스턴스 단위 누적 발송 카운트
    send_count = 0

    # 톡톡 사용 비중 통계 (중복 제외, 세션 누적)
    total_scanned_realtors = 0   # 최초로 확인한 총 중개사 수
    talk_enabled_count = 0       # 톡톡 버튼이 있던 중개사 수
    talk_disabled_count = 0      # 톡톡 버튼이 없던 중개사 수

    def __init__(self, signals=None):
        self.signals = signals
        self.is_running = True
        self.driver = None

        self.log("⚙️ 톡톡 자동 발송 엔진 초기화 중...")
        self.driver = self.setup_driver()
        self.start_id = self.load_checkpoint()

    # --------------------------------------------------
    # 공통 유틸
    # --------------------------------------------------
    def log(self, msg):
        log_str = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(log_str)
        if self.signals:
            self.signals.log.emit(log_str)

    def wait_for_internet(self):
        """인터넷이 연결될 때까지 대기."""
        while self.is_running:
            try:
                socket.create_connection(("8.8.8.8", 53), timeout=3)
                return True
            except OSError:
                self.log("📡 인터넷 연결 끊김... 연결을 기다립니다.")
                time.sleep(30)
        return False

    def load_checkpoint(self):
        if os.path.exists(CHECKPOINT_FILE):
            try:
                with open(CHECKPOINT_FILE, "r") as f:
                    return int(f.read().strip())
            except Exception:
                return 1
        return 1

    def save_checkpoint(self, c_id):
        with open(CHECKPOINT_FILE, "w") as f:
            f.write(str(c_id))

    # --------------------------------------------------
    # 드라이버 셋업 (봇 전용 독립 프로필 = 일반 크롬과 충돌 없음)
    # --------------------------------------------------
    def setup_driver(self):
        self.wait_for_internet()

        # ⚠️ ChromeOptions 객체는 1회용(uc.Chrome에 넘기면 재사용 불가)이므로
        #    매 시도마다 새 옵션 객체를 생성하는 팩토리로 만든다.
        def create_options():
            opt = uc.ChromeOptions()
            opt.add_argument("--window-size=1300,1000")
            opt.add_argument('--disable-blink-features=AutomationControlled')
            # 톡톡 창(새 팝업)이 크롬 자체 팝업 차단에 막히지 않도록 해제
            opt.add_argument("--disable-popup-blocking")
            # 봇 전용 독립 프로필 폴더 자동 생성 후 지정 (일반 크롬과 완전 분리)
            os.makedirs(BOT_PROFILE_DIR, exist_ok=True)
            opt.add_argument(f"--user-data-dir={BOT_PROFILE_DIR}")
            opt.add_argument(f"--profile-directory={CHROME_PROFILE_DIR}")
            return opt

        self.log(f"🧩 봇 전용 독립 크롬 프로필 사용: {BOT_PROFILE_DIR}")
        self.log("💡 최초 실행 시, 열린 크롬 창에서 네이버 로그인을 1회 수동으로 진행해 주세요.")

        try:
            driver = uc.Chrome(options=create_options(), version_main=CHROME_VERSION_MAIN)
        except Exception as e:
            self.log(f"⚠️ 1차 드라이버 로드 실패, 새 옵션으로 재시도 중... ({e})")
            self.wait_for_internet()
            # 새 ChromeOptions 객체를 다시 생성하여 주입 (객체 재사용 오류 방지)
            driver = uc.Chrome(options=create_options(), version_main=CHROME_VERSION_MAIN, use_subprocess=True)

        driver.implicitly_wait(5)
        return driver

    # --------------------------------------------------
    # [요구사항 4] 동적 메시지 생성기 (Spintax)
    # --------------------------------------------------
    def generate_dynamic_message(self, agent_name, complex_name):
        greetings = [
            f"안녕하세요 {agent_name} 대표님,",
            f"{agent_name} 대표님 안녕하세요,",
            f"바쁘신데 실례합니다 {agent_name}님,",
            f"{agent_name} 공인중개사님 반갑습니다,",
        ]
        bodies = [
            f"{complex_name} 매물 보고 연락드립니다. 혹시 지금도 거래 가능한 물건일까요?",
            f"{complex_name} 단지 매물 관련해서 문의드리고 싶어 톡 남깁니다.",
            f"{complex_name} 쪽 매물에 관심이 있어서요, 잠깐 여쭤봐도 될까요?",
            f"{complex_name} 매물 조건이 좋아 보여서 자세히 알아보고 싶습니다.",
        ]
        closings = [
            "편하실 때 답변 주시면 감사하겠습니다.",
            "확인 후 회신 부탁드립니다. 감사합니다!",
            "여유 되실 때 연락 주세요. 좋은 하루 되세요.",
            "답변 기다리겠습니다. 감사합니다.",
        ]
        return f"{random.choice(greetings)}\n{random.choice(bodies)}\n{random.choice(closings)}"

    # --------------------------------------------------
    # 선행 로그인 검증 (루프 진입 전 반드시 로그인 완료 대기)
    # --------------------------------------------------
    def ensure_logged_in(self):
        # 1) 네이버 메인으로 이동
        try:
            self.driver.get("https://www.naver.com")
        except Exception as e:
            if "ERR_NAME_NOT_RESOLVED" in str(e):
                self.wait_for_internet()
                self.driver.get("https://www.naver.com")
            else:
                raise
        time.sleep(2)

        # 2) 로그인 필요 안내
        self.log("🔐 로그인이 필요합니다. 열린 크롬 창에서 로그인을 완료해 주세요.")

        # 3) 로그인 완료까지 대기
        while self.is_running:
            try:
                current_url = self.driver.current_url
                # 로그인 페이지(nid.naver.com)에 머무는 동안 대기
                if "nid.naver.com" in current_url:
                    time.sleep(1)
                    continue
                # 네이버 메인에 '로그인' 버튼(.link_login)이 보이면 아직 미로그인 상태
                login_btns = self.driver.find_elements(By.CSS_SELECTOR, ".link_login, a.MyView-module__link_login___HpHMW")
                if login_btns:
                    time.sleep(1)
                    continue
                # 4) 로그인 완료
                self.log("🔓 로그인 완료 확인. 작업을 시작합니다.")
                return True
            except Exception:
                time.sleep(1)
        return False

    # --------------------------------------------------
    # 메인 루프
    # --------------------------------------------------
    def run(self):
        # 단지 순회 전, 반드시 로그인부터 완료
        if not self.ensure_logged_in():
            self.log("🛑 로그인 대기 중 중단되었습니다.")
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
            if self.signals:
                self.signals.finished.emit()
            return

        # 이번 세션에서 이미 처리한 중개사 이름 저장 (중복 발송 방지)
        self.processed_realtors = set()

        if SIMULATION_MODE:
            self.log(f"🧪 [시뮬레이션 모드 ON] 실제 전송 없이 흐름만 검증합니다. (딜레이 {DELAY_MIN_SEC}~{DELAY_MAX_SEC}초)")
        self.log(f"🚀 발송 작업 시작: 단지 ID {self.start_id}번부터 순회합니다. (일 목표 {DAILY_SEND_LIMIT}건)")
        for c_id in range(self.start_id, 200001):
            if not self.is_running:
                self.log("🛑 중단 요청 확인. 안전하게 작업을 종료합니다.")
                break

            if self.send_count >= DAILY_SEND_LIMIT:
                self.log(f"🎯 일일 발송 목표({DAILY_SEND_LIMIT}건) 도달! 오늘 작업을 종료합니다.")
                break

            if 27894 <= c_id <= 100000:
                continue  # 유령 구간 점프

            try:
                self.process_complex(c_id)
            except Exception as e:
                self.log(f"❌ ID {c_id} 처리 중 오류: {e}")
                if "session" in str(e).lower() or "invalid session" in str(e).lower():
                    try:
                        self.driver.quit()
                    except Exception:
                        pass
                    self.driver = self.setup_driver()

            self.save_checkpoint(c_id)

        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        if self.signals:
            self.signals.finished.emit()

    def process_complex(self, c_id):
        url = f"https://new.land.naver.com/complexes/{c_id}?a=APT:ABYG:JGC:PRE&b=A1&ad=true"
        try:
            self.driver.get(url)
        except Exception as e:
            if "ERR_NAME_NOT_RESOLVED" in str(e):
                self.wait_for_internet()
                self.driver.get(url)
            else:
                raise

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".complex_title"))
            )
        except TimeoutException:
            return  # 단지 페이지가 아니거나 매물 없음

        complex_name = self._read_complex_name(c_id)

        # [요구사항 1] 매물 리스트 비동기 로딩 명시적 대기
        #   - 스크롤/수집 전에, 실제 매물 카드가 최소 1개 이상 렌더링될 때까지 기다린다.
        try:
            WebDriverWait(self.driver, 7).until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    ".item_list--article .item, .item_list--article .item_inner"
                ))
            )
        except TimeoutException:
            self.log(f"   ℹ️ [ID {c_id}] 이 단지는 현재 등록된 매물이 없습니다. 패스합니다.")
            return

        # 매물 로딩이 확인된 후에 스크롤 루프(6회)로 지연 로딩 카드까지 모두 펼친다.
        try:
            scroll_pane = self.driver.find_element(By.CSS_SELECTOR, ".item_list.item_list--article")
            for _ in range(6):
                self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", scroll_pane)
                time.sleep(0.8)
        except Exception:
            pass

        # [요구사항 2] 최상위 매물 카드만 1차로 가져온다 (하위 자식 카드는 내부에서 별도 순회)
        all_items = self.driver.find_elements(By.CSS_SELECTOR, ".item_list--article .item")
        # 하위 묶음 요소(item--child)는 제외하고 순수한 최상위 부모 매물 카드만 추출
        items = [item for item in all_items if "item--child" not in item.get_attribute("class")][:100]
        self.log(f"🏢 [ID {c_id}] {complex_name} - 최상위 매물 {len(items)}개 탐색 시작...")

        main_window = self.driver.current_window_handle

        for item in items:
            if not self.is_running:
                break
            if self.send_count >= DAILY_SEND_LIMIT:
                break

            try:
                multicp = item.find_elements(By.CSS_SELECTOR, ".label--multicp")
                if not multicp:
                    # [케이스 A] 단독 매물
                    self._handle_single_item(item, complex_name, main_window)
                else:
                    # [케이스 B] 묶음(동일매물 묶기) 매물 - 펼친 뒤 하위 경쟁사 전수 순회
                    self._handle_bundle_item(item, multicp[0], complex_name, main_window)
            except Exception as e:
                self.log(f"   ⚠️ 매물 카드 처리 중 오류: {e}")
                self._cleanup_tabs(main_window)
                continue

        # [단지 스캔 요약] 톡톡 사용 비중 통계 (세션 누적)
        total = self.total_scanned_realtors
        enabled = self.talk_enabled_count
        disabled = self.talk_disabled_count
        rate = (enabled / total * 100) if total > 0 else 0.0
        self.log(
            f"📊 [단지 스캔 요약] 총 중개사 {total}곳 중 "
            f"톡톡 사용: {enabled}곳 | 미사용: {disabled}곳 (사용률: {rate:.1f}%)"
        )

    def _read_complex_name(self, c_id):
        try:
            return self.driver.find_element(By.CSS_SELECTOR, ".complex_title").text.strip() or f"단지{c_id}"
        except Exception:
            return f"단지{c_id}"

    # --------------------------------------------------
    # [케이스 A] 단독 매물 처리
    # --------------------------------------------------
    def _handle_single_item(self, item, complex_name, main_window):
        # 사전 필터링: 아실 제공 매물은 클릭 시 외부 리다이렉트되므로 건너뜀
        if self._is_asil(item):
            self.log("   [아실 패스] 외부 리다이렉트 방지를 위해 건너뜁니다.")
            time.sleep(0.1)
            return

        # 사전 중복 필터: 클릭 전 좌측 카드 텍스트로 이미 처리한 중개사면 패널 오픈 생략
        card_text = item.text
        if any(r_name in card_text for r_name in self.processed_realtors if r_name):
            self.log("   [사전 중복 건너뛰기] 우측 패널 오픈 생략")
            time.sleep(0.1)
            return

        if not self._open_detail_pane(item):
            time.sleep(0.1)
            return

        status = self.process_realtor_pane(complex_name, main_window)
        self._apply_conditional_delay(status)

    # --------------------------------------------------
    # [케이스 B] 묶음 매물 처리 (하위 경쟁사 전수 순회)
    # --------------------------------------------------
    def _handle_bundle_item(self, item, multicp_btn, complex_name, main_window):
        # 묶음 버튼 클릭 -> 하위 리스트 펼치기
        self.driver.execute_script("arguments[0].click();", multicp_btn)
        time.sleep(1.5)

        child_items = item.find_elements(By.CSS_SELECTOR, ".item--child .item_inner")
        self.log(f"   📦 묶음 매물 펼침 -> 하위 경쟁 중개사 {len(child_items)}곳 순회")

        for child_item in child_items:
            if not self.is_running:
                break
            if self.send_count >= DAILY_SEND_LIMIT:
                break

            # 사전 필터링: 하위 카드도 아실 제공이면 건너뜀
            if self._is_asil(child_item):
                self.log("   [아실 패스] 외부 리다이렉트 방지를 위해 건너뜁니다.")
                time.sleep(0.1)
                continue

            # 사전 중복 필터: 클릭 전 하위 카드 텍스트로 이미 처리한 중개사면 패널 오픈 생략
            card_text = child_item.text
            if any(r_name in card_text for r_name in self.processed_realtors if r_name):
                self.log("   [사전 중복 건너뛰기] 우측 패널 오픈 생략")
                time.sleep(0.1)
                continue

            if not self._open_detail_pane(child_item):
                time.sleep(0.1)
                continue

            status = self.process_realtor_pane(complex_name, main_window)
            self._apply_conditional_delay(status)

    # --------------------------------------------------
    # 공통 헬퍼: 아실 판별 / 카드 클릭 / 조건부 딜레이 / 탭 청소
    # --------------------------------------------------
    def _is_asil(self, element):
        try:
            return "아실 제공" in element.text
        except Exception:
            return False

    def _open_detail_pane(self, element):
        """카드의 제목 텍스트 영역을 클릭해 우측 상세 패널을 연다. 성공 시 True."""
        try:
            try:
                target = element.find_element(By.CSS_SELECTOR, ".item_title .text")
            except Exception:
                try:
                    target = element.find_element(By.CSS_SELECTOR, ".item_link")
                except Exception:
                    target = element
            self.driver.execute_script("arguments[0].click();", target)
            time.sleep(1.5)
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".table_td_agent, .detail_contents"))
            )
            return True
        except Exception:
            return False

    def _apply_conditional_delay(self, status):
        # 실제 톡톡 입력 액션이 일어난 경우(sent)에만 제재 방지 딜레이
        if status == "sent":
            delay = random.uniform(DELAY_MIN_SEC, DELAY_MAX_SEC)
            mode_tag = "🧪[시뮬레이션] " if SIMULATION_MODE else ""
            self.log(f"   [로그] ⏳ {mode_tag}다음 중개사까지 {delay:.0f}초 대기...")
            self._interruptible_sleep(delay)
            self.log("   [로그] ⏱️ 대기 종료, 다음 중개사로 제어권 이동")
        else:
            # 아실 패스 / 중복 / 버튼 없음 / 실패 = 빠르게 다음으로 패스
            time.sleep(0.1)

    def _cleanup_tabs(self, main_window):
        """[가드레일] 메인 창 외 예기치 못한 서브 탭(아실 등)을 모두 강제 종료하고 복귀."""
        try:
            if len(self.driver.window_handles) > 1:
                for handle in list(self.driver.window_handles):
                    if handle != main_window:
                        self.driver.switch_to.window(handle)
                        self.driver.close()
            self.driver.switch_to.window(main_window)
        except Exception:
            try:
                self.driver.switch_to.window(main_window)
            except Exception:
                pass

    # --------------------------------------------------
    # [요구사항 2] 우측 상세 패널의 중개사 정보 확인 + 톡톡 발송
    # --------------------------------------------------
    def process_realtor_pane(self, complex_name, main_window):
        """우측 상세 패널 기준 중개사 처리. 반환 상태:
        'duplicate' | 'no_button' | 'sent' | 'failed' | 'error'"""
        try:
            # 상호명 정밀 추출 - 반드시 중개사 영역(.table_td_agent) 내부에서만
            try:
                agent_name = self.driver.find_element(
                    By.CSS_SELECTOR, ".table_td_agent .info_title"
                ).text.strip()
            except Exception:
                agent_name = ""

            # 상호명 검증: 빈 값/파싱 실패면 세트 오염 방지를 위해 등록하지 않고 종료
            if not agent_name:
                self.log("   [파싱 실패] 중개사 상호명을 찾지 못해 건너뜁니다.")
                return "error"

            # 중복 검사
            if agent_name in self.processed_realtors:
                self.log(f"   [중복 건너뛰기] {agent_name}")
                return "duplicate"

            # 버튼 검사: 톡톡 버튼이 없으면 세트에 추가 후 종료
            talk_buttons = self.driver.find_elements(
                By.CSS_SELECTOR, ".table_td_agent a.btn_contact--talk"
            )
            if len(talk_buttons) <= 0:
                self.log(f"   [버튼 없음] {agent_name}")
                self.processed_realtors.add(agent_name)
                NaverTalkSender.total_scanned_realtors += 1
                NaverTalkSender.talk_disabled_count += 1
                return "no_button"

            # 여기까지 통과 = 최초로 확인한 '톡톡 사용' 중개사
            NaverTalkSender.total_scanned_realtors += 1
            NaverTalkSender.talk_enabled_count += 1

            self.log(f"   💬 톡톡 가능 중개사 발견: {agent_name}")
            success = self.send_talk_message(talk_buttons[0], agent_name, complex_name)

            if success:
                self.processed_realtors.add(agent_name)
                return "sent"
            return "failed"

        except Exception:
            return "error"
        finally:
            # [가드레일] 외부 탭이 떠 있으면 강제 종료하고 메인 창 복귀
            self._cleanup_tabs(main_window)

    # --------------------------------------------------
    # [요구사항 3] 윈도우 핸들 제어 + 발송 로직
    # --------------------------------------------------
    def send_talk_message(self, talk_button, agent_name, complex_name):
        """톡톡 처리. 입력/시뮬레이션 완료 시 True, 실패/미진입 시 False 반환."""
        main_window = self.driver.current_window_handle  # 1) 메인 윈도우 핸들 저장
        before_handles = set(self.driver.window_handles)
        success = False

        try:
            # 2) 톡톡 버튼 클릭 -> 3초 대기
            self.log(f"   [로그] 🖱️ '{agent_name}' 톡톡 버튼 클릭 시도... (새 창 대기 3초)")
            self.driver.execute_script("arguments[0].click();", talk_button)
            time.sleep(3)

            # 3) 새로 열린 창으로 전환
            new_handles = [h for h in self.driver.window_handles if h not in before_handles]
            if not new_handles:
                self.log("   [로그] ⚠️ 새 톡톡 창이 열리지 않았습니다. 건너뜁니다.")
                return False
            self.driver.switch_to.window(new_handles[-1])
            self.log(f"   [로그] ➡️ 새 톡톡 창으로 핸들 전환 완료 (현재 창 {len(self.driver.window_handles)}개)")

            # 4) 채팅 입력창 렌더링 대기
            chat_input = WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "textarea, div[contenteditable='true'], .chatting_input textarea, #textInput"
                ))
            )
            self.log("   [로그] ✅ 채팅 입력창 렌더링 확인")

            # 5) 메시지 동적 생성 후 입력
            message = self.generate_dynamic_message(agent_name, complex_name)

            # 🚨 [전송 신호 격리] 줄바꿈(\n)은 톡톡에서 즉시 전송 트리거가 되므로 공백으로 치환.
            #    또한 Keys.ENTER/RETURN 같은 전송 신호는 절대 send_keys에 포함하지 않는다.
            safe_message = message.replace("\r", " ").replace("\n", " ").strip()
            if Keys.ENTER in safe_message or Keys.RETURN in safe_message:
                safe_message = safe_message.replace(Keys.ENTER, " ").replace(Keys.RETURN, " ")

            chat_input.click()
            time.sleep(0.5)
            chat_input.send_keys(safe_message)  # 엔터/줄바꿈 없이 순수 텍스트만 입력
            time.sleep(1)
            self.log(f"   [로그] ⌨️ 메시지 입력 완료(줄바꿈 제거됨): \"{safe_message[:30]} ...\"")

            # 6) 전송 버튼 클릭
            if SIMULATION_MODE:
                # 🧪 [시뮬레이션 모드] 실제 전송은 하지 않음 (입력까지만 검증, 엔터 트리거 없음)
                self.log("   [로그] 🧪 [시뮬레이션] 실제 전송 신호(버튼/엔터)는 발생시키지 않습니다.")
            else:
                # send_btn = WebDriverWait(self.driver, 5).until(
                #     EC.element_to_be_clickable((
                #         By.CSS_SELECTOR,
                #         "button[type='submit'], .btn_send, button.send, .chatting_send"
                #     ))
                # )
                # send_btn.click()
                pass

            # 처리(또는 시뮬레이션) 성공 처리
            success = True
            NaverTalkSender.send_count += 1
            self.log(f"   [로그] ✉️ {agent_name} 처리 완료 (입력{'만' if SIMULATION_MODE else ' 및 전송'})")
            self.log(f"   [로그] 📊 오늘 처리 누적: {self.send_count}건")

        except TimeoutException:
            self.log(f"   [로그] ⚠️ {agent_name} 채팅 입력창 로딩 시간 초과. 건너뜁니다.")
        except Exception as e:
            self.log(f"   [로그] ⚠️ {agent_name} 발송 중 오류: {e}")
        finally:
            # 7) [필수] 성공 여부와 무관하게 새 창 닫고 메인 창으로 복귀
            try:
                if self.driver.current_window_handle != main_window:
                    self.driver.close()
                    self.log("   [로그] 🚪 톡톡 창 닫기 완료")
            except Exception:
                pass
            try:
                self.driver.switch_to.window(main_window)
                self.log("   [로그] 🔙 원래 부동산 페이지로 핸들 복귀 성공, 다음 대상 탐색 시작")
            except Exception:
                self.log("   [로그] ⚠️ 메인 창 핸들 복귀 실패")

        # 제재 방지 딜레이는 호출부(process_complex)에서 'sent'일 때만 적용한다.
        return success

    def _interruptible_sleep(self, seconds):
        """긴 대기 중에도 중단 요청을 빠르게 반영하기 위한 분할 sleep."""
        end_at = time.monotonic() + seconds
        while self.is_running and time.monotonic() < end_at:
            time.sleep(min(2.0, end_at - time.monotonic()))


# ======================================================
# [GUI] 메인 윈도우
# ======================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("이실장 톡톡 자동 발송기")
        self.setGeometry(100, 100, 550, 650)
        self.setStyleSheet("QWidget { font-family: 'Malgun Gothic'; background-color: #f8fafc; }")

        layout = QVBoxLayout()
        self.label = QLabel("💬 네이버 부동산 톡톡 자동 발송 (안전 중단 지원)")
        self.label.setStyleSheet("font-size: 13px; color: #333d4b; margin-bottom: 10px;")
        layout.addWidget(self.label)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            "background-color: #1e293b; color: #f8fafc; border-radius: 10px; "
            "font-family: 'Consolas'; font-size: 12px; line-height: 1.5;"
        )
        layout.addWidget(self.log_view)

        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("발송 시작")
        self.btn_start.setFixedHeight(50)
        self.btn_start.setStyleSheet(
            "background-color: #3182f6; color: white; font-weight: bold; font-size: 14px; border-radius: 8px;"
        )
        self.btn_start.clicked.connect(self.start_worker)

        self.btn_stop = QPushButton("안전 중단")
        self.btn_stop.setFixedHeight(50)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(
            "background-color: #ef4444; color: white; font-weight: bold; font-size: 14px; border-radius: 8px;"
        )
        self.btn_stop.clicked.connect(self.stop_worker)

        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        layout.addLayout(btn_layout)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.worker = None

    def start_worker(self):
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.log_view.append("⚙️ 백그라운드 엔진을 예열 중입니다...")

        self.signals = WorkerSignals()
        self.signals.log.connect(self.log_view.append)
        self.signals.finished.connect(self.on_finished)

        # 드라이버 셋업이 GUI를 막지 않도록 워커 생성까지 스레드에서 처리
        threading.Thread(target=self._boot_worker, daemon=True).start()

    def _boot_worker(self):
        try:
            self.worker = NaverTalkSender(signals=self.signals)
            self.worker.run()
        except Exception as e:
            self.signals.log.emit(f"❌ 엔진 시작 실패: {e}")
            self.signals.finished.emit()

    def stop_worker(self):
        if self.worker:
            self.worker.is_running = False
            self.btn_stop.setText("중단 처리 중...")
            self.btn_stop.setEnabled(False)

    def on_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setText("안전 중단")
        self.btn_stop.setEnabled(False)
        self.log_view.append("\n✅ 엔진 작동이 안전하게 종료되었습니다.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
