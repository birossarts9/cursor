import pandas as pd
import datetime
import random
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

input_file = "naver_market_report_2026_05.parquet"
output_file = "demo_market_report.parquet"

# 데이터 로드
df = pd.read_parquet(input_file)
df['수집일시'] = pd.to_datetime(df['수집일시'])

# 1. 단지명 설정
target_danji = "당산삼성래미안" 
df_demo = df[df['단지명'] == target_danji].copy()

if df_demo.empty:
    print(f"❌ '{target_danji}' 단지의 데이터가 없습니다.")
else:
    # 2. 1등 업체 자동 추출
    top_realtor = df_demo['부동산명'].value_counts().index[0]
    
    # 3. 1등 업체 변환 및 협회 필터 우회
    mask = df_demo['부동산명'] == top_realtor
    df_demo.loc[mask, 'CP사'] = "매경부동산"
    df_demo.loc[mask, '부동산명'] = "사랑공인중개사사무소"

    # [추가] 단지명을 데모 전용 가명으로 일괄 변경
    df_demo['단지명'] = "사랑동행복단지"
    if '매물묶음키' not in df_demo.columns:
        key_cols = ['단지명', '동/호수', '층/타입', '거래방식', '가격', 'CP사']
        for col in key_cols:
            if col not in df_demo.columns:
                df_demo[col] = ""
        df_demo['매물묶음키'] = (
            df_demo[key_cols].fillna("").astype(str).agg(" | ".join, axis=1)
        )

    # 4. 과거 히스토리 14일 치 확보
    max_date = df_demo['수집일시'].max()
    start_date = max_date - datetime.timedelta(days=14)
    df_demo = df_demo[df_demo['수집일시'] >= start_date]

    # [추가] '대기 권장' 상태를 자연스럽게 섞기 위한 인위적 데이터 조작
    # 오늘 자 경쟁사 수집 데이터의 50%를 삭제하여 '아직 갱신 안 함(대기 요망)' 상태를 유도함
    today = max_date.date()
    is_today = df_demo['수집일시'].dt.date == today
    is_competitor = df_demo['부동산명'] != "사랑공인중개사사무소"

    today_comp_data = df_demo[is_today & is_competitor]
    unique_bundles = list(today_comp_data['매물묶음키'].dropna().unique())

    # 절반 정도의 매물 묶음을 선택해서 오늘 치 경쟁사 데이터를 고의로 누락
    random.seed(42)
    bundles_to_delay = set(random.sample(unique_bundles, k=len(unique_bundles) // 2))

    drop_mask = is_today & is_competitor & df_demo['매물묶음키'].isin(bundles_to_delay)
    df_demo = df_demo[~drop_mask]

    # 저장
    df_demo.to_parquet(output_file, index=False)
    print(f"✅ 데모 데이터 생성 완료! (단지명: 사랑동행복단지 / 총 {len(df_demo)}행)")