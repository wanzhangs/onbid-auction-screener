# 온비드 공매 아파트 저평가 스크리너

온비드의 아파트 공매 최저입찰가를 국토부 실거래가와 비교해, 시세보다 싸게 나온 물건을 골라 보는 단일 페이지 리포트입니다.
`report.html`을 더블클릭하면 열립니다. 같은 내용의 표는 `data/results.csv`입니다.

수치는 1차 선별용이며 투자 권유가 아닙니다. 읽는 법과 한계는 페이지 맨 아래에 있습니다.

## 파일
- `update_trades.py` 국토부 실거래가(아파트 매매, 최근 1년)를 시도별 스냅샷 `data/trades/`로 받아 둡니다. 저장한 지 28일이 지난 시도만 다시 받습니다.
- `collect.py` 온비드의 아파트 공매 회차(앞으로 약 3개월)와 압류재산 임대차 정보를 수집하고, `data/trades/` 스냅샷과 매칭해 `data/results.json`·`data/results.csv`를 만듭니다. 국토부에는 접속하지 않습니다.
- `build_report.py` `data/results.json`을 `template.html`에 넣어 `report.html`을 만듭니다.

API 키와 로그인, LLM API는 쓰지 않습니다. 파이썬 3.11 표준 라이브러리만 사용합니다.

## PC에서 새로 고치기
```
git pull
python update_trades.py
python collect.py
python build_report.py
```

## GitHub Actions
| 워크플로 | 언제 | 하는 일 |
|---|---|---|
| **Update trades** | 매일 02:30 (한국 시간) | 28일 지난 시도의 실거래 스냅샷을 받아 `data/trades/`에 커밋합니다. 모두 최신이면 접속하지 않고 끝납니다. |
| **Refresh data** | 매주 월요일 04:00 | 온비드를 새로 수집하고 스냅샷과 매칭해 `data/`와 `report.html`을 커밋합니다. |
| **Build and publish report** | 푸시할 때, Refresh data가 끝났을 때 | 리포트를 다시 만들어 검사하고, GitHub Pages가 켜져 있으면 배포합니다. |

세 워크플로 모두 Actions 탭에서 수동으로도 실행할 수 있습니다.

GitHub 러너는 해외 IP라 온비드·국토부 사이트 접속이 러너에 따라 되기도 하고 안 되기도 합니다. 그래서 접속하지 못하면 새 러너에서 최대 3번까지 다시 시도하고(`*-once.yml`), 국토부는 받을 수 있는 시도만 받아 커밋한 뒤 다음 실행이 남은 시도를 이어서 받습니다. 스냅샷이 45일 넘게 갱신되지 않으면 Update trades가 실패로 표시됩니다. 그때는 PC에서 `python update_trades.py`를 실행해 푸시하면 됩니다.

### GitHub Pages
저장소 Settings → Pages → Source에서 **GitHub Actions**를 선택하면, 리포트가 `https://<계정>.github.io/onbid-auction-screener/`에 배포되고 Refresh data가 끝날 때마다 갱신됩니다. Pages가 꺼져 있으면 배포 단계만 건너뜁니다. Pages 사이트는 주소를 아는 누구나 볼 수 있고, 무료 요금제에서는 공개 저장소에서만 켤 수 있습니다.

수집 캐시(`data/onbid_raw.json`)는 저장소에 올리지 않습니다.
