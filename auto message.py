import pyautogui
import pyperclip
import gspread
from google.oauth2.service_account import Credentials
import time
import random

# ==========================================
# [설정 영역]
# ==========================================
JSON_FILE = 'service_account_key.json'  
SHEET_URL = 'https://docs.google.com/spreadsheets/d/1yEllJWWNwsd5FMvvgwSIvA46j10XU_8MxpRAWcs-ba8/edit'
TARGET_GID = 120518916  

POS_NEW_MSG      = (1862, 186)  
POS_RECIPIENT    = (1181, 244)  
POS_IMG_ICON     = (1208, 984)  
POS_BROWSE_PC    = (1095, 922)  
POS_MESSAGE_BOX  = (1212, 928)  
POS_SEND_BTN     = (1851, 979)  
POS_BACK_LIST    = (1054, 134)  

START_ROW = 1066  
MAX_SEND = 300 

DEFAULT_IMAGE_NAME = "cut-toon" 

pyautogui.FAILSAFE = True

def paste_text(text):
    pyperclip.copy(str(text))
    time.sleep(0.5)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.5)

def main():
    print("구글 스프레드시트 연결 중...")
    try:
        scope = ['https://www.googleapis.com/auth/spreadsheets']
        creds = Credentials.from_service_account_file(JSON_FILE, scopes=scope)
        client = gspread.authorize(creds)
        doc = client.open_by_url(SHEET_URL)
        worksheet = doc.get_worksheet_by_id(TARGET_GID) 
        all_data = worksheet.get_all_values()
    except Exception as e:
        print(f"연결 실패: {e}")
        return

    send_count = 0
    print(f"{START_ROW}행부터 작업을 시작합니다.")
    time.sleep(3)

    for i in range(START_ROW - 1, len(all_data)):
        if send_count >= MAX_SEND: break
        
        row = all_data[i]
        current_row_num = i + 1
        
        phone_number = row[7].strip() if len(row) > 7 else ""
        message_content = row[10].strip() if len(row) > 10 else ""
        image_name = row[15].strip() if len(row) > 15 else "" # P열

        if not phone_number or phone_number == 'nan': continue

        # 번호 보정
        if phone_number.endswith('.0'): phone_number = phone_number[:-2]
        if phone_number.startswith('10') and len(phone_number) == 10:
            phone_number = '0' + phone_number

        if not image_name:
            image_name = DEFAULT_IMAGE_NAME

        print(f"[{current_row_num}행] {phone_number} 세팅 중...")

        try:
            # 1. 수신인 입력
            pyautogui.click(POS_NEW_MSG)
            time.sleep(1.5)
            pyautogui.click(POS_RECIPIENT)
            paste_text(phone_number)
            pyautogui.press('enter')
            time.sleep(1.5)

            # 2. 텍스트 입력 (전송 버튼은 아직 누르지 않음)
            pyautogui.click(POS_MESSAGE_BOX)
            paste_text(message_content)
            time.sleep(0.8)

            # 3. 이미지 첨부
            if image_name:
                pyautogui.click(POS_IMG_ICON)
                time.sleep(1.2)
                pyautogui.click(POS_BROWSE_PC)
                time.sleep(2.0) 
                
                paste_text(image_name)
                pyautogui.press('enter')
                print(f"   - 이미지({image_name}) 업로드 대기 중...")
                time.sleep(5.0) # 파일이 완전히 올라갈 때까지 대기
            
            # 4. 최종 발송 (텍스트와 이미지를 한 번에 전송)
            pyautogui.click(POS_SEND_BTN)
            print(f"   - 발송 버튼 클릭")
            time.sleep(2.5) # 발송 완료 대기

            # 5. 목록 복귀
            pyautogui.click(POS_BACK_LIST)

            worksheet.update_cell(current_row_num, 17, "성공")
            send_count += 1
            print(f"   - 다음으로 이동")

        except Exception as e:
            print(f"   - 에러: {e}")
            try:
                worksheet.update_cell(current_row_num, 17, "실패")
                pyautogui.click(POS_BACK_LIST)
            except: pass

        time.sleep(random.uniform(5.0, 10.0))

    print("전체 작업 종료.")

if __name__ == "__main__":
    main()