"""Next.js 파일 기반 라우팅 — 규칙팩(패턴 매칭)으로는 못 잡는 '경로 = 파일 위치' 규약을 해석한다.

- App Router: app/ (없으면 src/app/). page → 페이지, route → HTTP 핸들러(내보낸 GET/POST... 이름)
  (group) 은 경로에서 빠지고, @slot 은 병렬 라우트, (.)x (..)x (...)x 는 가로채기 라우트, _x 는 비공개 폴더
- Pages Router: pages/ (없으면 src/pages/). pages/api/** 는 HTTP, _app/_document/_error 는 앱 껍데기
- 루트의 app/·pages/ 가 있으면 src/app·src/pages 는 Next.js 가 무시한다 → '비활성 라우트'로 표시
- middleware, 'use server' 서버 액션, next.config 의 rewrites/redirects/i18n 도 진입점·미확인으로 수집
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

PAGE_EXT = {".tsx", ".ts", ".jsx", ".js", ".mdx", ".md"}
HANDLER_EXT = {".ts", ".js", ".tsx", ".jsx", ".mjs"}
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
EXPORT_METHOD_RE = re.compile(
    r"export\s+(?:async\s+)?(?:function|const|let)\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b"
    r"|export\s*\{([^}]*)\}"
)
USE_SERVER_RE = re.compile(r"""^\s*['"]use server['"]""")
EXPORT_FN_RE = re.compile(r"export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)|export\s+const\s+(\w+)\s*=\s*async")


def is_next_project(repo: Path, files: list[str]) -> bool:
    for f in files:
        if PurePosixPath(f).name == "package.json" and "node_modules" not in f:
            try:
                text = (repo / f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(r'"next"\s*:', text):
                return True
    return False


def _roots(files: list[str], name: str) -> tuple[str | None, list[str]]:
    """활성 라우터 루트와 무시되는 루트 목록."""
    has = {root for root in (name, f"src/{name}") if any(f.startswith(root + "/") for f in files)}
    if name in has:
        return name, sorted(has - {name})
    if f"src/{name}" in has:
        return f"src/{name}", []
    return None, []


def _segments_to_path(segments: list[str]) -> tuple[str, list[str]]:
    """폴더 세그먼트 → URL 경로. 특이 세그먼트 표시(notes)도 함께."""
    parts, notes = [], []
    for seg in segments:
        if seg.startswith("(") and seg.endswith(")") and not re.match(r"^\(\.{1,3}\)", seg):
            continue  # 라우트 그룹
        if seg.startswith("@"):
            notes.append(f"병렬 라우트 슬롯 {seg}")
            continue
        m = re.match(r"^\((\.{1,3}|\.\.\)\(\.\.)\)(.*)$", seg)
        if m:
            notes.append(f"가로채기 라우트 ({m.group(1)}){m.group(2)}")
            parts.append(m.group(2))
            continue
        parts.append(seg)
    return "/" + "/".join(p for p in parts if p), notes


def _read(repo: Path, path: str) -> str:
    try:
        return (repo / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def scan(repo: Path, files: list[str]) -> dict:
    """반환: {"features": [...], "unknowns": [...]}  — features 는 inventory 가 근거와 함께 기록한다."""
    features, unknown_list = [], []
    code_files = [f for f in files if "node_modules/" not in f]

    for router, active_kind in (("app", "app"), ("pages", "pages")):
        active, ignored = _roots(code_files, router)
        for root in ignored:
            unknown_list.append({
                "question": f"{root}/ 는 루트 {router}/ 가 있어 Next.js 가 무시하는 라우터 폴더 — 비활성 코드(정리 대상인지 확인)",
                "domain": "entrypoint", "scope": f"nextjs-ignored:{root}", "severity": "medium", "required_level": 0,
            })
        for root in ([active] if active else []) + ignored:
            inactive = root != active
            for f in code_files:
                if not f.startswith(root + "/"):
                    continue
                p = PurePosixPath(f)
                rel = PurePosixPath(f[len(root) + 1:])
                if any(seg.startswith("_") for seg in rel.parts[:-1]):
                    continue  # 비공개 폴더
                stem, ext = p.stem, p.suffix
                if active_kind == "app":
                    route, notes = _segments_to_path(list(rel.parts[:-1]))
                    if stem == "page" and ext in PAGE_EXT:
                        features.append(_feature("page", route, f, 1, None, notes, inactive))
                    elif stem == "route" and ext in HANDLER_EXT:
                        text = _read(repo, f)
                        methods = []
                        for m in EXPORT_METHOD_RE.finditer(text):
                            if m.group(1):
                                methods.append((m.group(1), _line_of(text, m.start())))
                            else:
                                for name in re.findall(r"\bas\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b|\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", m.group(2)):
                                    methods.append((name[0] or name[1], _line_of(text, m.start())))
                        if not methods:
                            unknown_list.append({"question": f"{f} 라우트 핸들러에서 내보낸 HTTP 메서드를 찾지 못함", "domain": "entrypoint",
                                                 "scope": f"nextjs-route:{f}", "severity": "medium", "required_level": 0})
                        for method, line in methods:
                            features.append(_feature("http", route, f, line, method, notes, inactive))
                    elif stem in {"layout", "template"} and ext in PAGE_EXT:
                        features.append(_feature("layout", route, f, 1, None, notes, inactive))
                    if any("가로채기" in n for n in notes) and stem == "page":
                        unknown_list.append({"question": f"{f} 는 가로채기 라우트 — 어떤 탐색에서 모달로 뜨는지 실행 흐름 확인 필요",
                                             "domain": "entrypoint", "scope": f"nextjs-intercept:{f}", "severity": "low", "required_level": 3})
                else:
                    if ext not in PAGE_EXT:
                        continue
                    parts = list(rel.parts[:-1]) + ([] if stem == "index" else [stem])
                    route = "/" + "/".join(parts)
                    if stem in {"_app", "_document", "_error"}:
                        features.append(_feature("app-shell", route, f, 1, None, [], inactive))
                    elif rel.parts and rel.parts[0] == "api":
                        features.append(_feature("http", route, f, 1, "ANY", ["메서드는 req.method 분기로 결정 — 코드 확인 필요"], inactive))
                    else:
                        features.append(_feature("page", route, f, 1, None, [], inactive))

    # middleware 는 라우터와 같은 위치(루트 또는 src)에 있어야 적용된다
    app_root, _ = _roots(code_files, "app")
    pages_root, _ = _roots(code_files, "pages")
    uses_src = (app_root or pages_root or "").startswith("src/")
    for f in code_files:
        p = PurePosixPath(f)
        if p.stem == "middleware" and p.suffix in HANDLER_EXT and str(p.parent) in {".", "src"}:
            text = _read(repo, f)
            matcher = re.search(r"matcher\s*:\s*(\[[^\]]*\]|['\"][^'\"]*['\"])", text)
            inactive = (str(p.parent) == "src") != uses_src
            features.append(_feature("middleware", matcher.group(1) if matcher else "*", f, 1, None,
                                     ([] if matcher else ["matcher 없음 — 모든 요청에 적용"])
                                     + (["라우터 폴더와 위치가 달라 적용되지 않음"] if inactive else []), inactive))
            if inactive:
                unknown_list.append({"question": f"{f} 는 활성 라우터({app_root or pages_root})와 위치가 달라 Next.js 가 적용하지 않는 미들웨어 — 의도 확인 필요",
                                     "domain": "entrypoint", "scope": f"nextjs-middleware:{f}", "severity": "high", "required_level": 0})
        if p.suffix in HANDLER_EXT | {".tsx", ".jsx"}:
            text = _read(repo, f)
            if not text:
                continue
            first = next((ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("//")), "")
            if USE_SERVER_RE.match(first):
                for m in EXPORT_FN_RE.finditer(text):
                    features.append(_feature("server-action", m.group(1) or m.group(2), f, _line_of(text, m.start()), "POST", [], False))
            elif re.search(r"""\n\s*['"]use server['"]""", text):
                unknown_list.append({"question": f"{f} 에 함수 안 'use server' 서버 액션이 있음 — 액션 목록을 단위 분석에서 확인",
                                     "domain": "entrypoint", "scope": f"nextjs-inline-action:{f}", "severity": "low", "required_level": 0})

    for f in code_files:
        if PurePosixPath(f).name.startswith("next.config."):
            text = _read(repo, f)
            for key in ("rewrites", "redirects", "basePath", "i18n", "trailingSlash", "assetPrefix"):
                # 객체 키(key:) 또는 메서드(async rewrites() {) 로 쓰였을 때만 — 문자열·경로 속 단어는 제외
                if re.search(rf"(?<![\w/.'\"-])(?:async\s+)?{key}\s*(?::|\(\s*\)\s*\{{)", text):
                    unknown_list.append({"question": f"{f} 의 {key} 설정이 실제 URL 을 바꿈 — 경로 목록에 반영 필요",
                                         "domain": "entrypoint", "scope": f"nextjs-config:{key}", "severity": "medium", "required_level": 0})
            m = re.search(r"require\(['\"]([^'\"]*(?:intl|i18n)[^'\"]*)['\"]\)", text)
            if m:
                unknown_list.append({"question": f"{f} 가 {m.group(1)} 플러그인으로 로캘 접두를 붙임 — [locale] 세그먼트·미들웨어와 함께 확인",
                                     "domain": "entrypoint", "scope": "nextjs-config:i18n-plugin", "severity": "low", "required_level": 0})
    return {"features": features, "unknowns": unknown_list}


def _feature(kind: str, route: str, path: str, line: int, method: str | None, notes: list[str], inactive: bool) -> dict:
    return {"kind": kind, "name": route, "path": path, "line": line, "method": method,
            "notes": notes + (["비활성 라우터 폴더 (Next.js 가 무시)"] if inactive else []), "inactive": inactive}
