"""평가 채점 — report.json 이 정답 사실을 근거와 함께 담고 있는지 확인한다.

사용: python eval/score.py eval/meilisearch.yml <report.json>
"""
from __future__ import annotations

import json
import sys

import yaml


def score(spec: dict, report: dict) -> dict:
    ev_paths: dict[str, set[str]] = {}
    for unit in report["units"]:
        for m in unit["members"]:
            ev_paths.setdefault(unit["id"], set()).add(m["path"])
    results = []
    for fact in spec["facts"]:
        ok, why = False, []
        if "keywords" in fact:
            hits = [c for c in report["claims"]
                    if fact["path"] in ev_paths.get(c["unit_id"], set())
                    and any(k.lower() in c["text"].lower() for k in fact["keywords"])]
            ok = bool(hits)
            why.append(f"결론 {len(hits)}개")
        if "feature_kind" in fact:
            feats = [f for f in report["features"]
                     if f["path"] == fact["path"] and f["kind"] == fact["feature_kind"] and not f.get("in_test")]
            ok = (ok or "keywords" not in fact) and len(feats) >= fact["feature_min"]
            why.append(f"진입점 {len(feats)}/{fact['feature_min']}")
        if "unknown_domain" in fact:
            unk = [u for u in report["unknowns"] if u["domain"] == fact["unknown_domain"] and fact["path"] in u["question"]]
            ok = bool(unk)
            why.append(f"미확인 {len(unk)}개")
        results.append({"id": fact["id"], "ok": ok, "detail": ", ".join(why)})
    passed = sum(r["ok"] for r in results)
    return {"passed": passed, "total": len(results), "recall": round(passed / len(results), 2), "results": results}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    spec = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
    report = json.load(open(sys.argv[2], encoding="utf-8"))
    out = score(spec, report)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["passed"] == out["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
