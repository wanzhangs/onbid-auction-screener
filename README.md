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
- **Refresh data** (`.github/workflows/refresh.yml`): Actions 탭에서 수동으로 실행하면 데이터를 새로 수집해 커밋합니다. 정기 실행은 파일 안의 `schedule` 주석을 풀면 켜집니다. GitHub 러너는 해외 IP라 온비드·국토부 사이트가 응답하지 않을 수 있고, 그 경우 PC에서 실행해 푸시합니다.
