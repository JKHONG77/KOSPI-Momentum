# 코스피 모멘텀 대시보드

Jegadeesh & Titman (1993) "Returns to Buying Winners and Selling Losers" 방식을 코스피에 적용한 대시보드입니다.

- `scripts/fetch.py` : KRX Open API에서 일별 데이터 수집 (이미 받은 날짜는 건너뜀)
- `scripts/analyze.py` : 백테스트·현재 상위/하위 종목·업종 순위 계산 → `docs/data.json`
- `scripts/encrypt.py` : 결과를 비밀번호로 암호화 → `docs/data.enc`
- `docs/index.html` : 대시보드 화면 (GitHub Pages, 비밀번호 입력)
- `.github/workflows/update.yml` : 평일 18:30, 08:30(KST) 자동 실행

필요한 Secrets: `KRX_AUTH_KEY` (KRX 인증키), `DASHBOARD_PASSWORD` (대시보드 비밀번호)
