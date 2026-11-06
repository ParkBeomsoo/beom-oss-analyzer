"""라이선스 스캔(내장, 가벼움) — 커스텀·재배포 전에 '이 파일을 가져다 써도 되는가'를 판단할 재료.

근거 세 갈래:
  spdx       파일 머리의 SPDX-License-Identifier
  text       LICENSE·COPYING 파일 본문 / 소스 머리 주석의 대표 문구
  manifest   package.json · pyproject.toml · Cargo.toml · pom.xml · setup.cfg · composer.json · *.gemspec 의 license 필드

범위: 루트 라이선스는 저장소 전체, 하위 디렉터리의 LICENSE 는 그 하위 트리(vendored·third_party)에 적용.
파일 헤더·SPDX 는 그 파일에만 적용. 범위보다 좁은 쪽이 우선이다.

ScanCode 같은 정밀 텍스트 대조가 아니라 대표 문구 매칭이라, 문구를 고친 변형 라이선스·이중 라이선스 문장은 놓칠 수 있다.
의존성(설치되는 패키지)의 라이선스는 다루지 않는다 → 미확인으로 남긴다.
"""
from __future__ import annotations

import json
import re
import tomllib
from collections import defaultdict
from pathlib import PurePosixPath

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import evidence, ledger, unknowns
from oss_analyzer.core.levels import L0

# (SPDX id, 필수 문구들) — 위에서부터 먼저 맞는 것 (구체적인 것이 위)
TEXT_RULES: list[tuple[str, list[str]]] = [
    ("AGPL-3.0", ["GNU AFFERO GENERAL PUBLIC LICENSE"]),
    ("LGPL-3.0", ["GNU LESSER GENERAL PUBLIC LICENSE", "Version 3"]),
    ("LGPL-2.1", ["GNU LESSER GENERAL PUBLIC LICENSE", "Version 2.1"]),
    ("LGPL", ["GNU Lesser General Public License"]),
    ("GPL-3.0", ["GNU GENERAL PUBLIC LICENSE", "Version 3"]),
    ("GPL-2.0", ["GNU GENERAL PUBLIC LICENSE", "Version 2"]),
    ("GPL", ["GNU General Public License"]),
    ("SSPL-1.0", ["Server Side Public License"]),
    ("BUSL-1.1", ["Business Source License"]),
    ("Elastic-2.0", ["Elastic License 2.0"]),
    ("Commons-Clause", ["Commons Clause"]),
    ("MPL-2.0", ["Mozilla Public License", "2.0"]),
    ("EPL-2.0", ["Eclipse Public License", "2.0"]),
    ("EPL-1.0", ["Eclipse Public License"]),
    ("CDDL-1.0", ["COMMON DEVELOPMENT AND DISTRIBUTION LICENSE"]),
    ("Apache-2.0", ["Apache License", "Version 2.0"]),
    ("BSL-1.0", ["Boost Software License"]),
    ("Unlicense", ["This is free and unencumbered software released into the public domain"]),
    ("CC0-1.0", ["CC0 1.0 Universal"]),
    ("ISC", ["Permission to use, copy, modify, and/or distribute this software for any purpose"]),
    ("BSD-3-Clause", ["Redistribution and use in source and binary forms", "Neither the name"]),
    ("BSD-2-Clause", ["Redistribution and use in source and binary forms"]),
    ("MIT", ["Permission is hereby granted, free of charge"]),
    ("Zlib", ["This software is provided 'as-is', without any express or implied"]),
]

CATEGORY = {
    "permissive": {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "ISC", "Zlib", "BSL-1.0", "Unlicense",
                   "CC0-1.0", "PSF-2.0", "Python-2.0", "X11", "MIT-0", "BlueOak-1.0.0"},
    "weak-copyleft": {"LGPL", "LGPL-2.1", "LGPL-3.0", "MPL-2.0", "EPL-1.0", "EPL-2.0", "CDDL-1.0"},
    "strong-copyleft": {"GPL", "GPL-2.0", "GPL-3.0", "AGPL-3.0"},
    "source-available": {"SSPL-1.0", "BUSL-1.1", "Elastic-2.0", "Commons-Clause"},
}
ALIASES = {  # 매니페스트·분류자에 흔한 표기
    "mit license": "MIT", "apache 2.0": "Apache-2.0", "apache-2": "Apache-2.0", "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0", "bsd license": "BSD-3-Clause", "bsd": "BSD-3-Clause", "new bsd": "BSD-3-Clause",
    "isc license (iscl)": "ISC", "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "gnu general public license v3 (gplv3)": "GPL-3.0", "gnu general public license v2 (gplv2)": "GPL-2.0",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0", "gnu affero general public license v3": "AGPL-3.0",
    "the unlicense (unlicense)": "Unlicense", "python software foundation license": "PSF-2.0",
}
SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.\-+ ()]+?)\s*(?:\*/|-->|$)", re.M)
LICENSE_FILE_RE = re.compile(r"^(?:LICEN[CS]E|COPYING|UNLICENSE|NOTICE)(?:[.\-_][\w.\-]*)?$", re.I)
CLASSIFIER_RE = re.compile(r"License :: (?:OSI Approved :: )?([^\"'\n]+)")
SEVERITY = {"strong-copyleft": "high", "source-available": "high", "weak-copyleft": "medium", "unknown": "medium"}


def normalize(raw: str) -> str:
    s = raw.strip().strip("\"'")
    s = re.sub(r"(?i)-(only|or-later)$", "", s).replace("+", "")
    low = s.lower()
    if low in ALIASES:
        return ALIASES[low]
    for ids in CATEGORY.values():
        for i in ids:
            if i.lower() == low:
                return i
    return s


def category_of(expr: str) -> str:
    """SPDX 식(OR/AND)은 OR 이면 가장 느슨한 선택지, AND 면 가장 엄격한 것으로 본다."""
    rank = ["permissive", "weak-copyleft", "strong-copyleft", "source-available"]
    parts_or = [p for p in re.split(r"\s+OR\s+|/", expr) if p.strip()]
    best = None
    for alt in parts_or:
        worst = None
        for lic in re.split(r"\s+AND\s+|\s+WITH\s+", alt.strip("() ")):
            lic = normalize(lic)
            cat = next((c for c, ids in CATEGORY.items() if lic in ids), "unknown")
            if cat == "unknown":
                worst = "unknown"
                break
            worst = cat if worst is None or rank.index(cat) > rank.index(worst) else worst
        if worst == "unknown":
            continue
        best = worst if best is None or rank.index(worst) < rank.index(best) else best
    return best or "unknown"


def match_text(text: str) -> str | None:
    flat = " ".join(text.split())
    for spdx, phrases in TEXT_RULES:
        if all(" ".join(ph.split()).lower() in flat.lower() for ph in phrases):
            return spdx
    return None


def _manifest(path: str, text: str) -> list[tuple[str, int]]:
    name = PurePosixPath(path).name
    out: list[tuple[str, int]] = []

    def line_of(needle: str) -> int:
        for i, ln in enumerate(text.splitlines(), start=1):
            if needle in ln:
                return i
        return 1
    try:
        if name in ("package.json", "composer.json"):
            data = json.loads(text)
            lic = data.get("license") or data.get("licenses")
            for item in lic if isinstance(lic, list) else [lic]:
                val = item.get("type") if isinstance(item, dict) else item
                if val:
                    out.append((str(val), line_of('"license')))
        elif name == "pyproject.toml":
            data = tomllib.loads(text)
            lic = data.get("project", {}).get("license") or data.get("tool", {}).get("poetry", {}).get("license")
            if isinstance(lic, dict):
                lic = lic.get("text") or (f"file:{lic['file']}" if lic.get("file") else None)
            if lic and not str(lic).startswith("file:"):
                out.append((str(lic), line_of("license")))
            for cls in data.get("project", {}).get("classifiers", []):
                m = CLASSIFIER_RE.match(cls)
                if m:
                    out.append((m.group(1), line_of(cls)))
        elif name == "Cargo.toml":
            data = tomllib.loads(text)
            lic = data.get("package", {}).get("license")
            if isinstance(lic, str):
                out.append((lic, line_of("license")))
        elif name == "setup.cfg" or name.endswith(".gemspec"):
            m = re.search(r"""^\s*(?:\w+\.)?license\s*[=:]\s*["']?([^"'\n]+)""", text, re.M)
            if m:
                out.append((m.group(1).strip(), _line(text, m.start())))
        elif name == "pom.xml":
            for m in re.finditer(r"<license>\s*<name>([^<]+)</name>", text):
                out.append((m.group(1).strip(), _line(text, m.start())))
    except (ValueError, tomllib.TOMLDecodeError, AttributeError):
        return []
    return out


def _line(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


SPLIT_RE = re.compile(r"(?i)\b(?:part|parts|portions?|some files)\s+of\s+this\s+(?:work|software|repository|project)")


def _split_root(proj: dict, findings: list[dict]) -> None:
    """루트 LICENSE 가 '일부는 제한 라이선스(별도 파일), 나머지는 허용형(별도 파일)' 이라고 말하면
    기본은 허용형, 제한 라이선스는 헤더로 표시된 파일에만 (scope=marked) 적용한다. 예: 저장소 전체 MIT + 일부 디렉터리만 BUSL-1.1."""
    root_text = [fd for fd in findings if fd["scope"] == "dir:" and fd["source"] == "text"]
    main = next((fd for fd in root_text if re.fullmatch(r"(?i)(licen[cs]e|copying)(\.(md|txt))?", PurePosixPath(fd["path"]).name)), None)
    if main is None:
        return
    try:
        text = "\n".join(evidence.read_lines(proj, main["path"])[:200])
    except (OSError, ToolError):
        return
    if not SPLIT_RE.search(text):
        return
    others = [fd for fd in root_text if fd is not main]
    perm = [fd for fd in others if fd["category"] == "permissive"]
    restricted = [fd for fd in others if fd["category"] not in ("permissive", "unknown")]
    if not perm or not restricted:
        return
    main.update(license=perm[0]["license"], category="permissive",
                note=f"분할 라이선스 — 기본 {perm[0]['license']}, {', '.join(r['license'] for r in restricted)} 는 "
                     "헤더로 표시된 파일(또는 그 디렉터리)에만")
    for r in restricted:
        r["scope"] = "marked"


def scan(conn, proj: dict) -> dict:
    files = [dict(r) for r in conn.execute("SELECT path, category FROM file").fetchall()]
    findings: list[dict] = []
    for f in files:
        path, cat = f["path"], f["category"]
        if cat in ("secret", "binary"):
            continue
        try:
            lines = evidence.read_lines(proj, path)
        except (OSError, ToolError):
            continue
        name = PurePosixPath(path).name
        parent = str(PurePosixPath(path).parent)
        scope_dir = "" if parent == "." else parent
        if LICENSE_FILE_RE.match(name):
            lic = match_text("\n".join(lines[:400]))
            if lic or not name.upper().startswith("NOTICE"):
                findings.append({"path": path, "license": lic or "unrecognized", "source": "text", "line": 1,
                                 "scope": f"dir:{scope_dir}"})
            continue
        head = "\n".join(lines[:40])
        m = SPDX_RE.search(head)
        if m:
            findings.append({"path": path, "license": m.group(1).strip(), "source": "spdx",
                             "line": _line(head, m.start()), "scope": "file"})
        elif cat in ("code", "test", "vendored", "generated"):
            lic = match_text(head)
            if lic:
                findings.append({"path": path, "license": lic, "source": "text", "line": 1, "scope": "file"})
        for val, line in _manifest(path, "\n".join(lines)):
            findings.append({"path": path, "license": val, "source": "manifest", "line": line, "scope": f"dir:{scope_dir}"})

    for fd in findings:
        fd["category"] = category_of(fd["license"]) if fd["license"] != "unrecognized" else "unknown"
    _split_root(proj, findings)

    # 저장소 기준 라이선스 = 루트 범위 발견들
    root = [fd for fd in findings if fd["scope"] == "dir:"]
    declared = sorted({fd["license"] for fd in root})
    root_cats = {fd["category"] for fd in root}
    project_cat = "permissive" if root_cats == {"permissive"} else (sorted(root_cats)[0] if root_cats else "unknown")

    issues = []
    for fd in findings:
        # 허용형은 어디에 있어도 문제 없음, 저장소와 같은 계열(예: GPL 저장소 안의 GPL 파일)도 그대로 둔다
        if fd["category"] == "permissive" or (fd["category"] == project_cat != "unknown"):
            continue
        issues.append(fd)
    split = next((fd for fd in root if fd.get("note", "").startswith("분할")), None)
    root_spdx = {normalize(fd["license"]) for fd in root if fd["category"] != "unknown"}
    if len({category_of(x) for x in root_spdx}) > 1 or (len(root_spdx) > 1 and not all(category_of(x) == "permissive" for x in root_spdx)):
        issues.append({"path": "(root)", "license": " / ".join(sorted(root_spdx)), "category": "unknown",
                       "source": "mismatch", "line": 1, "scope": "dir:",
                       "note": "루트 LICENSE·매니페스트가 서로 다른 라이선스를 선언"})

    with ledger.tx(conn):
        conn.execute("DELETE FROM license_finding")
        conn.execute("DELETE FROM unknown WHERE origin = 'license'")
        for fd in findings:
            ev_id = None
            try:
                ev = evidence.record(conn, proj, provider="pattern", operation=f"license:{fd['source']}",
                                     parameters={"path": fd["path"]}, result={"license": fd["license"], "category": fd["category"]},
                                     locations=[{"path": fd["path"], "start_line": fd["line"], "end_line": fd["line"]}],
                                     confidence="inferred",
                                     limitations=["대표 문구 매칭 — 변형 라이선스·이중 라이선스 문장은 놓칠 수 있음"])
                ev_id = ev["evidence_id"]
            except ToolError:
                pass
            fd["evidence_id"] = ev_id
            conn.execute("INSERT INTO license_finding (path, license, category, source, line, scope, evidence_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (fd["path"], fd["license"], fd["category"], fd["source"], fd["line"], fd["scope"], ev_id))
        for fd in issues:
            if fd["path"] == "(root)":
                q = f"루트 라이선스 선언이 서로 다름: {fd['license']}"
            else:
                where = "디렉터리 " + fd["scope"][4:] if fd["scope"].startswith("dir:") else "파일"
                q = f"{fd['path']} ({where}) 의 라이선스 {fd['license']} [{fd['category']}] 가 저장소 라이선스 {declared or '미확인'} 와 다름 — 사용·재배포 조건 확인"
            unknowns.record(conn, question=q, domain="license", scope=f"license:{fd['path']}",
                            severity=SEVERITY.get(fd["category"], "medium"), required_level=L0, origin="license",
                            supporting_evidence_ids=[fd["evidence_id"]] if fd.get("evidence_id") else [],
                            recommended_probes=[{"operation": "agent-read", "rationale": "라이선스 전문·저작권 표시 확인"},
                                                {"operation": "scancode", "rationale": "정밀 텍스트 대조가 필요하면"}])
        if not root:
            unknowns.record(conn, question="저장소 루트 라이선스를 찾지 못함 (LICENSE 파일·매니페스트 license 필드 없음)",
                            domain="license", scope="license:root", severity="high", required_level=L0, origin="license")
        manifests = [f["path"] for f in files if PurePosixPath(f["path"]).name in (
            "package.json", "pyproject.toml", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod",
            "requirements.txt", "composer.json", "Gemfile")]
        if manifests:
            unknowns.record(conn, question="의존성 패키지의 라이선스는 내장 스캐너 범위 밖 (설치되는 패키지 메타데이터 필요)",
                            domain="license", scope="license:dependencies", severity="low", required_level=L0, origin="license",
                            recommended_probes=[{"operation": "scancode", "rationale": "의존성까지 포함한 전수 스캔"},
                                                {"operation": "package-manager", "rationale": "npm ls·pip-licenses·cargo-deny 등"}])

    by_cat: dict[str, int] = defaultdict(int)
    for fd in findings:
        by_cat[fd["category"]] += 1
    return {"declared": declared, "project_category": project_cat, **({"split": split["note"]} if split else {}), "findings": len(findings), "by_category": dict(by_cat),
            "issues": [{k: fd.get(k) for k in ("path", "license", "category", "source", "line", "scope", "note")} for fd in issues],
            "note": "파일 헤더·하위 LICENSE 가 없는 파일은 루트 라이선스를 따른다고 본다 (저작권 표시는 별도 확인)"}


def list_(conn) -> dict:
    return {"items": ledger.rows_to_dicts(conn.execute("SELECT * FROM license_finding ORDER BY scope, path").fetchall())}
