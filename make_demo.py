import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import Counter

def create_demo_data():
    # 2026년 5월 18일 종료 기준 (오늘 22시까지)
    kst_now = datetime(2026, 5, 18, 23, 0, 0)
    start_date = datetime(2026, 5, 5, 0, 0, 0)
    
    # 1. 꽉 찬 타임라인을 위한 매물 10개 생성
    bundles = [
        {"id": f"BND_{100+i}", "동": f"10{1+i%5}동", "형태": "중/84㎡", "가격": f"{10+i}억", "방식": "매매"}
        for i in range(10)
    ]

    me = "사랑공인중개사사무소"
    comps = ["스피드부동산", "황금공인중개사", "대박부동산", "행운부동산", "미래부동산"]
    realtors = [me] + comps
    
    # 2. 🎯 AI 효율 총점 정확히 82% 달성 & 우상향 트렌드 세팅
    # 14일간 총 1120회의 노출 중 정확히 918회(82.0%) 상위권 진입
    sarang_daily = [52, 50, 56, 52, 60, 66, 64, 72, 68, 76, 72, 78, 74, 78]
    
    # 3. 🏆 0점 없는 경쟁사 점유율 배분 (63%, 58%, 49%, 27%, 22%)
    comp_targets = {"스피드부동산": 700, "황금공인중개사": 650, "대박부동산": 550, "행운부동산": 300, "미래부동산": 242}
    comp_remains = dict(comp_targets)
    daily_comp_quotas = {d: {c: 0 for c in comps} for d in range(14)}
    
    for d in range(14):
        slots_needed = 240 - sarang_daily[d]
        for _ in range(slots_needed):
            valid_comps = [c for c in comps if comp_remains[c] > 0 and daily_comp_quotas[d][c] < 80]
            if not valid_comps:
                valid_comps = [c for c in comps if daily_comp_quotas[d][c] < 80]
            weights = [comp_remains[c] if comp_remains[c] > 0 else 1 for c in valid_comps]
            total_w = sum(weights)
            probs = [w/total_w for w in weights]
            chosen = np.random.choice(valid_comps, p=probs)
            daily_comp_quotas[d][chosen] += 1
            comp_remains[chosen] -= 1

    # 4. 📡 다채로운 패턴과 상세보기 리얼리티 (광고/대기 권장 믹스)
    # 각 경쟁사별로 갱신 빈도를 다르게 주어 AI 분석기가 다양한 태그를 뿜어내도록 유도
    renewal_schedules = {
        "스피드부동산": {d: np.random.choice([8, 10, 12]) for d in range(14)}, # 🔥 매일 갱신
        "황금공인중개사": {d: 14 for d in [0, 2, 4, 6, 8, 10, 12]},              # ⚡ 2일에 1번
        "대박부동산": {d: 8 for d in [1, 4, 8, 11, 13]},                       # 🚶 3~4일에 1번
        "행운부동산": {d: 16 for d in [3, 10]},                                # 🐢 주 1~2회
        "미래부동산": {d: 12 for d in [5]},                                    # 💤 비정기적
        "사랑공인중개사사무소": {d: 8 for d in range(14)}
    }
    
    # [오늘 5/18(Day 13)의 리얼리티 강제 부여]
    renewal_schedules["스피드부동산"][13] = 10  # 🟢 오늘 광고 완료 (10시)
    # 황금, 행운, 미래는 오늘 갱신 없음 -> 🔴 아직 활동 전 (주의)
    
    current_seeds = {r: {b: 0 for b in range(10)} for r in realtors}
    records = []
    
    for d in range(14):
        current_date = start_date + timedelta(days=d)
        top3_pool = [me] * sarang_daily[d]
        for c in comps:
            top3_pool.extend([c] * daily_comp_quotas[d][c])
            
        counts = Counter(top3_pool)
        sorted_realtors = []
        for r, count in counts.most_common():
            sorted_realtors.extend([r] * count)
            
        groups = [[] for _ in range(80)]
        for i, r in enumerate(sorted_realtors):
            groups[i % 80].append(r)
            
        np.random.shuffle(groups)
            
        snapshot_idx = 0
        for h in [8, 10, 12, 14, 16, 18, 20, 22]:
            dt = current_date.replace(hour=h)
            
            # 고유번호(UID) 갱신 로직
            for r in realtors:
                if d in renewal_schedules[r] and h == renewal_schedules[r][d]:
                    for b in range(10):
                        current_seeds[r][b] += 1
                        
            for b_idx in range(10):
                top3 = groups[snapshot_idx]
                snapshot_idx += 1
                
                bottom3 = [r for r in realtors if r not in top3]
                np.random.shuffle(top3)
                np.random.shuffle(bottom3)
                final_ranks = top3 + bottom3
                b = bundles[b_idx]
                
                for rank, realtor in enumerate(final_ranks, start=1):
                    seed = current_seeds[realtor][b_idx]
                    uid = f"U_{b['id']}_{realtor[:2]}_{seed}"
                    
                    records.append({
                        "수집일시": dt,
                        "단지명": "사랑동행복단지",
                        "부동산명": realtor,
                        "묶음내순위": f"{rank}위",
                        "전체순위": f"{rank}위",
                        "노출형태": "묶음",
                        "매물번호": f"M_{b['id']}_{b_idx}",
                        "동/호수": b["동"],
                        "거래방식": b["방식"],
                        "가격": b["가격"],
                        "층/타입": b["형태"],
                        "확인일자": dt.strftime("%y.%m.%d."),
                        "고유번호": uid,
                        "CP사": "네이버부동산"
                    })

    df = pd.DataFrame(records)
    filename = f"naver_market_report_{kst_now.strftime('%Y_%m')}.parquet"
    df.to_parquet(filename, index=False)
    df.to_parquet("demo_market_report.parquet", index=False)
    print(f"✅ [성공] 매물 10개, 총점 82% 우상향, 다채로운 상세보기 패턴이 적용된 데이터({len(df)}행) 생성 완료.")

if __name__ == "__main__":
    create_demo_data()