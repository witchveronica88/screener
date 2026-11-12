# screener

레버리지 ETF·개별주 스크리너와 반등·과열 상황판. 하루 한 번(한국 오전 7시쯤) 미국 확정 종가로 계산한다.

## 파일

| 파일 | 하는 일 |
|---|---|
| `index.html` | 화면 (상황 탭 · 종목 탭). 아래 JSON들을 읽기만 한다 |
| `etfs.json` | 종목 탭의 레버리지 ETF 목록 — 이름·기초자산·설명 등. **ETF 추가·수정은 여기 한 곳** |
| `tickers.txt` | 직접 추가한 개별주 (한 줄에 하나) |
| `market.py` | 가격 받기 · 지표(%K·CCI·RSI) · 노란 점 · 회복 여부 — 아래 두 파일이 같이 쓴다 |
| `prices.py` | 종가 수집 → `data.json` (종목 탭) |
| `desk.py` | 상황판 판정 → `desk.json`, 그날 표시한 판정 기록 → `desk_log.json` |
| `fund.json` | 개별주 재무 (손으로 넣는다) |
| `tests.py` | 계산 시험 (`python3 tests.py`, 인터넷 없이 돈다) |

## 자동 실행

- `.github/workflows/prices.yml` — 월~금 22:00 UTC에 `prices.py` → `desk.py` 순서로 돌고 결과를 커밋한다. `desk.py`가 실패해도 종가는 저장된다.
- `.github/workflows/test.yml` — 계산 코드나 종목 목록이 바뀌면 `tests.py`를 돌린다.
