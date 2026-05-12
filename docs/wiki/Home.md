# TOP RANK AI Dashboard Wiki

부동산 광고/갱신 분석 대시보드의 운영 문서입니다.  
현재 실제 대시보드 구동의 중심은 `app.py`, `ranking_logic.py`, `data_fetcher.py` 입니다.

---

## 1) 프로젝트 개요

이 프로젝트는 네이버 부동산 수집 데이터를 기반으로 다음을 수행합니다.

- 매물별 갱신 빈도 계산
- 경쟁사/자사 활동량 비교
- 전략 추천 시간 계산
- 대시보드 형태로 시각화

---

## 2) 핵심 파일

- `app.py`
  - Streamlit UI 진입점
  - 사용자 필터/화면 렌더링
  - 집계 결과를 카드/테이블/차트로 표시
- `ranking_logic.py`
  - 갱신 빈도, 등급, Heat Level, 추천 시간 등 핵심 비즈니스 로직
- `data_fetcher.py`
  - 데이터 로드/전처리
  - 중복(그림자 매물) 제거 및 기본 정규화

---

## 3) 데이터 처리 흐름 (요약)

1. `data_fetcher.py`에서 최신 리포트 로딩
2. 전처리/정규화/중복 제거
3. `ranking_logic.py`에서 기간별 집계 및 점수화
4. `app.py`에서 필터 반영 후 시각화 출력

---

## 4) 최근 반영된 핵심 정책

- 갱신 횟수는 `확인일자` 기준 고유 날짜 수(`nunique`) 방식으로 집계
- 고스트/중복성 데이터에 대한 필터링 로직 보강
- 데이터 로드 기간/집계 기간 정합성(최근 N일) 관리 강화

---

## 5) 문서 목차

- [App 구조 (`app.py`)](App-Structure)
- [랭킹 로직 (`ranking_logic.py`)](Ranking-Logic)
- [데이터 로딩/정제 (`data_fetcher.py`)](Data-Fetcher)
- [운영 가이드](Operations-Guide)
- [트러블슈팅](Troubleshooting)
