# 온비드 공매 아파트 저평가 스크리너

`report.html`을 더블클릭하면 결과 페이지가 열립니다. 같은 내용의 표는 `data/results.csv`입니다.

- `collect.py` 온비드의 아파트 공매 회차(앞으로 약 3개월), 국토부 아파트 매매 실거래 CSV(최근 1년), 압류재산 임대차 정보를 수집해 `data/results.json`과 `data/results.csv`를 만듭니다.
- `build_report.py` `data/results.json`을 `template.html`에 넣어 `report.html`을 만듭니다.

새로 고치려면:

```
python collect.py
python build_report.py
```

API 키와 로그인은 쓰지 않습니다. 국토부 CSV는 하루 100회 다운로드 제한이 있어 `data/molit/`에 받아 둔 파일을 재사용합니다(날짜가 바뀌면 시도별로 새로 받습니다, 약 100MB).

수집 캐시(`data/molit`, `data/onbid_raw.json`)는 용량이 커서 저장소에 올리지 않습니다.

## GitHub Actions
- **Build report** (`.github/workflows/build.yml`): main에 푸시하면 문법 검사 후 `report.html`을 다시 만들고 `data/results.csv`와 함께 아티팩트로 올립니다.
- **Refresh data** (`.github/workflows/refresh.yml`): Actions 탭에서 수동으로 실행하면 데이터를 새로 수집해 커밋합니다. 매주 월요일 04:00(한국 시간)에 자동으로도 실행됩니다. 국토부 실거래가 사이트는 해외 IP(GitHub 러너)에서 접속되지 않아, 러너는 온비드 공매 목록과 임대차 정보만 새로 받고 시세 비교에는 저장소의 실거래 스냅샷(`data/trades.csv.gz`)을 씁니다. 스냅샷은 PC에서 `python collect.py`를 실행해 푸시하면 갱신됩니다(한 달에 한 번 정도 권장).
