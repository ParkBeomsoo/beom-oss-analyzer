"""라우트 카탈로그 채점 — 사람이 작성한 정답 목록(JSON)과 비교한다.

사용: python eval/routes_vs_expected.py <project_id> <expected.json> <key>
expected.json: {"<key>": ["GET /path/{}", ...]} — 경로 매개변수는 {} 로 표기
"""
from __future__ import annotations

import json
import re
import sys

from oss_analyzer.config import load_settings
from oss_analyzer.core import project


def norm(path: str) -> str:
    path = re.sub(r"\{[^}]*\}|:[A-Za-z_]\w*|\[[^\]]+\]|<[^>]+>", "{}", path)
    return path.rstrip("/") or "/"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    pid, expected_file, key = sys.argv[1:4]
    truth = {(e.split(" ", 1)[0], norm(e.split(" ", 1)[1])) for e in json.load(open(expected_file, encoding="utf-8"))[key]}
    conn, _ = project.load(load_settings(), pid)
    got = set()
    for r in conn.execute("SELECT method, full_path FROM feature WHERE kind = 'http' AND in_test = 0 AND full_path IS NOT NULL"):
        for m in (r["method"] or "").split(","):
            if m.strip():
                got.add((m.strip().upper(), norm(r["full_path"])))
    hit = truth & got
    print(json.dumps({
        "truth": len(truth), "extracted": len(got), "matched": len(hit),
        "missing": sorted(f"{m} {p}" for m, p in truth - got), "extra": sorted(f"{m} {p}" for m, p in got - truth),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
