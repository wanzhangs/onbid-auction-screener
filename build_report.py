"""data/results.json을 template.html에 넣어 report.html을 만든다 (더블클릭으로 열리는 단일 파일)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    data = json.loads((HERE / "data" / "results.json").read_text(encoding="utf-8"))
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (HERE / "template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", blob, 1)
    (HERE / "report.html").write_text(html, encoding="utf-8")
    print(f"report.html 생성 ({len(html) / 1e6:.1f}MB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
