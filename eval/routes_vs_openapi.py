"""라우트 카탈로그 채점 — 프로젝트가 공개한 OpenAPI 명세(정답)와 추출한 HTTP 엔드포인트를 비교한다.

사용: python eval/routes_vs_openapi.py <project_id> <openapi.json> [--prefix /api]
테스트·예제 코드의 진입점(in_test)은 제외한다. 경로 매개변수 이름이 달라도 같은 자리면 같은 경로로 본다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from oss_analyzer.config import load_settings
from oss_analyzer.core import project

METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def norm(path: str) -> str:
    path = re.sub(r"\{[^}]+\}|:[A-Za-z_]\w*|\[[^\]]+\]", "{}", path)
    return path.rstrip("/") or "/"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("openapi")
    ap.add_argument("--prefix", default="")
    args = ap.parse_args()

    spec = json.load(open(args.openapi, encoding="utf-8"))
    truth = {(m.upper(), norm(args.prefix + p)) for p, ops in spec["paths"].items() for m in ops if m in METHODS}
    conn, _ = project.load(load_settings(), args.project_id)
    got = set()
    for r in conn.execute("SELECT method, full_path FROM feature WHERE kind = 'http' AND in_test = 0 AND full_path IS NOT NULL"):
        for m in (r["method"] or "").split(","):
            if m:
                got.add((m.strip().upper(), norm(r["full_path"])))
    hit = truth & got
    out = {
        "truth": len(truth), "extracted": len(got), "matched": len(hit),
        "recall": round(len(hit) / len(truth), 3) if truth else None,
        "precision": round(len(hit) / len(got), 3) if got else None,
        "missing": sorted(f"{m} {p}" for m, p in truth - got),
        "extra": sorted(f"{m} {p}" for m, p in got - truth),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
