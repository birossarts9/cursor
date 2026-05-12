# 랭킹 로직 (`ranking_logic.py`)

## 역할

대시보드 핵심 지표 계산 모듈입니다.

- 매물/부동산 단위 갱신 빈도 계산
- 등급 산정
- Heat Level 계산
- 갱신 추천 시간 계산

## 핵심 원칙

- 갱신 횟수는 `확인일자`의 고유 값 수(`nunique`) 중심으로 계산
- 일요일 제외 등 비즈니스 규칙은 중앙 함수에서 일관 적용
- 복잡한 상태 추적보다 재현 가능한 집계 기준 우선

## 주요 함수 (개념)

- `_count_confirm_change_events(...)`
  - 갱신 이벤트 카운팅 핵심 함수
- `count_renewal_events_for_bundle(...)`
  - 특정 매물 키 단위 갱신 횟수 산출
- `calculate_ad_efficiency_with_grades(...)`
  - 빈도/경쟁도 기반 등급 계산
- `precalculate_ai_strategy(...)`
  - 추천 시간대 계산

## 수정 시 주의사항

- 카운팅 기준 변경 시 반드시 기존 샘플과 비교 검증
- 기간(window) 정의와 필터 로직(일요일 제외 등)을 함께 점검
- app/UI에서 호출하는 함수 시그니처 호환성 유지
