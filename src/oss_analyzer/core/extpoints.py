"""확장 포인트 — 코어를 고치지 않고 동작을 바꿀 수 있는 곳. 커스텀할 때 '수정'보다 먼저 추천한다.

종류:
  interface       추상 타입(ABC·Protocol·interface·trait·abstract class)과 저장소 안의 구현체
  registry        등록 함수·데코레이터 (register_*·add_* 등이 모듈 컬렉션에 넣는 것)와 등록 위치
  middleware      요청 파이프라인 훅 (app.use(fn)·add_middleware·@app.middleware·before_request)
  config-key      환경변수·설정 키 읽기 (os.environ·process.env·env::var·System.getenv·@Value)
  overridable-bean  Spring @ConditionalOnMissingBean (같은 타입 빈을 정의하면 교체됨)
  spi             Java META-INF/services 등록
  dynamic-loader  이름 문자열로 코드를 불러오는 곳 (importlib·Class.forName·require(변수)) → 미확인

정규식 근사(L0)다. 구현체 연결은 타입 이름 기반이라 동명 타입이 있으면 오탐할 수 있고,
Go 인터페이스는 메서드 집합 비교로 근사한다 (limitations 에 남김).
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import PurePosixPath

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import evidence, ledger, unknowns
from oss_analyzer.core.levels import L3

FAMILY = {"python": "py", "javascript": "js", "typescript": "js", "java": "jvm", "kotlin": "jvm",
          "go": "go", "rust": "rust", "csharp": "cs"}

PY_CLASS = re.compile(r"^[ \t]*class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:", re.M)
PY_ABSTRACT_BASE = re.compile(r"\b(?:abc\.)?(?:ABC|ABCMeta|Protocol)\b")
PY_DEF = re.compile(r"^def\s+(\w+)\s*\(", re.M)
REGISTRY_NAME = re.compile(r"^(register\w*|add_\w+|use_\w+|install\w*|subscribe\w*|hook\w*|plugin\w*)$")
# JS 는 camelCase. use* 는 React 훅 관례와 겹쳐서 제외
JS_REGISTRY_NAME = re.compile(r"^(?:register|add|install|subscribe|hook|plugin)(?:[A-Z_]\w*)?$")
REGISTRY_BODY = re.compile(r"\.append\(|\.add\(|\.setdefault\(|\.update\(|\.push\(|\.set\(|\.insert\(|\w+\s*\[[^\]]+\]\s*=(?!=)")
PY_MIDDLEWARE = re.compile(r"(\w+)\.add_middleware\(\s*(\w+)|@(\w+)\.(middleware|before_request|after_request|exception_handler)\b")
PY_CONFIG = re.compile(r"""os\.(?:environ(?:\.get)?\s*[\[(]|getenv\s*\()\s*["']([\w.\-]+)["']""")
PY_DYNAMIC = re.compile(r"\b(importlib\.import_module|__import__|entry_points)\s*\(")

JS_ABSTRACT = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(interface|abstract\s+class)\s+(\w+)", re.M)
JS_CLASS = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)(?:\s+extends\s+([\w.]+))?(?:\s+implements\s+([\w.,\s]+?))?\s*\{", re.M)
JS_REGISTRY = re.compile(r"^\s*(?:export\s+)?(?:function\s+(\w+)\s*\(|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\()", re.M)
JS_MIDDLEWARE = re.compile(r"\b(\w+)\.use\(\s*(?![\"'`/])([\w.]+)")
JS_CONFIG = re.compile(r"""process\.env(?:\.([A-Za-z_]\w*)|\[\s*["']([\w.\-]+)["']\s*\])""")
JS_DYNAMIC = re.compile(r"\brequire\(\s*(?![\"'`])\w|\bimport\(\s*(?![\"'`])\w")

JVM_ABSTRACT = re.compile(r"^\s*(?:public\s+|protected\s+|private\s+)?(?:sealed\s+)?(interface|abstract\s+class)\s+(\w+)", re.M)
JVM_CLASS = re.compile(r"^\s*(?:public\s+|protected\s+|private\s+|final\s+|open\s+|data\s+)*class\s+(\w+)[^{]*?(?:extends\s+([\w.]+))?[^{]*?(?:implements\s+([\w.,\s<>]+?))?\s*\{", re.M)
JVM_CONFIG = re.compile(r"""@Value\(\s*"\$\{([\w.\-]+)|System\.getenv\(\s*"(\w+)"|@ConfigurationProperties\(\s*(?:prefix\s*=\s*)?"([\w.\-]+)\"""")
JVM_BEAN = re.compile(r"@ConditionalOnMissingBean[\s\S]{0,400}?\b(?:public\s+)?([\w<>]+)\s+(\w+)\s*\(")
JVM_DYNAMIC = re.compile(r"\b(Class\.forName|ServiceLoader\.load)\s*\(")

GO_IFACE = re.compile(r"^type\s+(\w+)\s+interface\s*\{(.*?)^\}", re.M | re.S)
GO_METHOD = re.compile(r"^func\s*\(\s*\w*\s*\*?(\w+)(?:\[[^\]]*\])?\s*\)\s*(\w+)\s*\(", re.M)
GO_CONFIG = re.compile(r"""os\.(?:Getenv|LookupEnv)\(\s*"(\w+)\"""")

RS_TRAIT = re.compile(r"^\s*(?:pub(?:\([\w:]+\))?\s+)?(?:unsafe\s+)?trait\s+(\w+)", re.M)
RS_IMPL = re.compile(r"^\s*impl(?:<[^>{]*>)?\s+(?:[\w:]+::)?(\w+)(?:<[^>{]*>)?\s+for\s+([^\s{<]+)", re.M)
RS_CONFIG = re.compile(r"""(?:env::var(?:_os)?|env!|option_env!)\(\s*"(\w+)\"""")


def _line(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _ep_id(kind: str, name: str, path: str, line: int) -> str:
    return "xp_" + hashlib.sha256(f"{kind}|{name}|{path}|{line}".encode()).hexdigest()[:16]


class _Collector:
    def __init__(self) -> None:
        self.points: dict[str, dict] = {}
        self.dynamic: list[dict] = []
        # 구현 후보: 가족(언어) → 기반 타입 이름 → [{name, path, line}]
        self.subclasses: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        self.go_methods: dict[str, set[str]] = defaultdict(set)   # 타입 → 메서드 집합
        self.go_type_loc: dict[str, dict] = {}

    def add(self, kind: str, name: str, lang: str | None, path: str, line: int, note: str | None = None) -> dict:
        pid = _ep_id(kind, name, path, line)
        return self.points.setdefault(pid, {"id": pid, "kind": kind, "name": name, "language": lang, "path": path,
                                            "line": line, "implementations": [], "usages": [], "note": note})


def _collect_py(c: _Collector, path: str, lang: str, text: str) -> None:
    for m in PY_CLASS.finditer(text):
        name, bases = m.group(1), m.group(2) or ""
        line = _line(text, m.start())
        if PY_ABSTRACT_BASE.search(bases) or "metaclass=ABCMeta" in bases.replace(" ", ""):
            c.add("interface", name, lang, path, line)
        for b in re.split(r"\s*,\s*", bases):
            b = b.split("[")[0].strip().split(".")[-1]
            if re.fullmatch(r"\w+", b) and "=" not in b:
                c.subclasses["py"][b].append({"name": name, "path": path, "line": line})
    lines = text.splitlines()
    for m in PY_DEF.finditer(text):
        name = m.group(1)
        if not REGISTRY_NAME.match(name):
            continue
        start = _line(text, m.start())
        body = []
        for ln in lines[start:start + 30]:
            if ln.strip() and not ln.startswith((" ", "\t")):
                break
            body.append(ln)
        if REGISTRY_BODY.search("\n".join(body)):
            c.add("registry", name, lang, path, start)
    for m in PY_MIDDLEWARE.finditer(text):
        name = m.group(2) or f"@{m.group(3)}.{m.group(4)}"
        c.add("middleware", name, lang, path, _line(text, m.start()))
    for m in PY_CONFIG.finditer(text):
        c.add("config-key", m.group(1), lang, path, _line(text, m.start()))
    for m in PY_DYNAMIC.finditer(text):
        c.dynamic.append({"path": path, "line": _line(text, m.start()), "call": m.group(1)})


def _collect_js(c: _Collector, path: str, lang: str, text: str) -> None:
    for m in JS_ABSTRACT.finditer(text):
        c.add("interface", m.group(2), lang, path, _line(text, m.start()))
    for m in JS_CLASS.finditer(text):
        name, line = m.group(1), _line(text, m.start())
        for b in [m.group(2) or ""] + re.split(r"\s*,\s*", m.group(3) or ""):
            b = b.strip().split(".")[-1]
            if b:
                c.subclasses["js"][b].append({"name": name, "path": path, "line": line})
    lines = text.splitlines()
    for m in JS_REGISTRY.finditer(text):
        name = m.group(1) or m.group(2)
        if not name or not JS_REGISTRY_NAME.match(name):
            continue
        start = _line(text, m.start())
        if REGISTRY_BODY.search("\n".join(lines[start - 1:start + 15])):
            c.add("registry", name, lang, path, start)
    for m in JS_MIDDLEWARE.finditer(text):
        c.add("middleware", m.group(2), lang, path, _line(text, m.start()))
    for m in JS_CONFIG.finditer(text):
        c.add("config-key", m.group(1) or m.group(2), lang, path, _line(text, m.start()))
    for m in JS_DYNAMIC.finditer(text):
        c.dynamic.append({"path": path, "line": _line(text, m.start()), "call": m.group(0).split("(")[0]})


def _collect_jvm(c: _Collector, path: str, lang: str, text: str) -> None:
    for m in JVM_ABSTRACT.finditer(text):
        c.add("interface", m.group(2), lang, path, _line(text, m.start()))
    for m in JVM_CLASS.finditer(text):
        name, line = m.group(1), _line(text, m.start())
        for b in [m.group(2) or ""] + re.split(r"\s*,\s*", m.group(3) or ""):
            b = b.split("<")[0].strip().split(".")[-1]
            if b:
                c.subclasses["jvm"][b].append({"name": name, "path": path, "line": line})
    for m in JVM_CONFIG.finditer(text):
        c.add("config-key", next(g for g in m.groups() if g), lang, path, _line(text, m.start()))
    for m in JVM_BEAN.finditer(text):
        c.add("overridable-bean", m.group(1), lang, path, _line(text, m.start()),
              note=f"빈 메서드 {m.group(2)} — 같은 타입 빈을 정의하면 교체됨")
    for m in JVM_DYNAMIC.finditer(text):
        c.dynamic.append({"path": path, "line": _line(text, m.start()), "call": m.group(1)})


def _collect_go(c: _Collector, path: str, lang: str, text: str) -> None:
    for m in GO_IFACE.finditer(text):
        methods = sorted(set(re.findall(r"^\s*([A-Z]\w*|[a-z]\w*)\s*\(", m.group(2), re.M)))
        p = c.add("interface", m.group(1), lang, path, _line(text, m.start()))
        p["_methods"] = methods
    for m in GO_METHOD.finditer(text):
        c.go_methods[m.group(1)].add(m.group(2))
        c.go_type_loc.setdefault(m.group(1), {"name": m.group(1), "path": path, "line": _line(text, m.start())})
    for m in GO_CONFIG.finditer(text):
        c.add("config-key", m.group(1), lang, path, _line(text, m.start()))


def _collect_rust(c: _Collector, path: str, lang: str, text: str) -> None:
    for m in RS_TRAIT.finditer(text):
        c.add("interface", m.group(1), lang, path, _line(text, m.start()))
    for m in RS_IMPL.finditer(text):
        c.subclasses["rust"][m.group(1)].append({"name": m.group(2), "path": path, "line": _line(text, m.start())})
    for m in RS_CONFIG.finditer(text):
        c.add("config-key", m.group(1), lang, path, _line(text, m.start()))


COLLECTORS = {"py": _collect_py, "js": _collect_js, "jvm": _collect_jvm, "go": _collect_go, "rust": _collect_rust}


def _usages(points: list[dict], sources: dict[str, str], limit: int = 50) -> None:
    """등록 함수·미들웨어 이름이 쓰인 위치 (정의 줄 제외, 이름 기반)."""
    for p in points:
        if p["kind"] != "registry":
            continue
        pat = re.compile(rf"(?:@{re.escape(p['name'])}\b|\b{re.escape(p['name'])}\s*\()")
        for path, text in sources.items():
            for m in pat.finditer(text):
                line = _line(text, m.start())
                if path == p["path"] and line == p["line"]:
                    continue
                if len(p["usages"]) < limit:
                    p["usages"].append({"path": path, "line": line})


def build(conn, project: dict, files: list[dict]) -> dict:
    """인벤토리 끝에서 호출. 확장 포인트 표를 새로 채우고 동적 로딩은 미확인으로 남긴다."""
    c = _Collector()
    sources: dict[str, str] = {}
    for f in files:
        fam = FAMILY.get(f["language"] or "")
        if fam is None or f["category"] not in ("code", "test"):
            continue
        try:
            text = "\n".join(evidence.read_lines(project, f["path"]))
        except (OSError, ToolError):
            continue
        sources[f["path"]] = text
        if f["category"] == "code" and fam in COLLECTORS:
            COLLECTORS[fam](c, f["path"], f["language"], text)
    # Java SPI 등록 파일
    for f in files:
        if "/META-INF/services/" in f"/{f['path']}" and f["category"] != "secret":
            try:
                impls = [ln.strip() for ln in evidence.read_lines(project, f["path"]) if ln.strip() and not ln.startswith("#")]
            except (OSError, ToolError):
                continue
            p = c.add("spi", PurePosixPath(f["path"]).name, None, f["path"], 1)
            p["implementations"] = [{"name": i, "path": f["path"], "line": n + 1} for n, i in enumerate(impls)]

    limitations = {"interface": "구현체는 타입 이름으로 연결 — 동명 타입·별칭 import 는 오탐/누락 가능"}
    for p in c.points.values():
        if p["kind"] != "interface":
            continue
        fam = FAMILY.get(p["language"] or "")
        if fam == "go":
            need = set(p.pop("_methods", []))
            if need:
                p["implementations"] = [c.go_type_loc[t] for t, ms in c.go_methods.items() if need <= ms]
            p["note"] = "Go 구조적 타이핑 — 메서드 이름 집합으로 근사 (시그니처 미비교)"
        else:
            p["implementations"] = list(c.subclasses[fam].get(p["name"], []))
            # 구현의 구현 (추상 클래스 → 하위 클래스) 한 단계 더
            for impl in list(p["implementations"]):
                p["implementations"] += [x for x in c.subclasses[fam].get(impl["name"], []) if x not in p["implementations"]]
        if not p["implementations"]:
            p["note"] = (p.get("note") or "") + " 저장소 안 구현 없음 — 외부 구현용이거나 동적 생성".strip()
    points = list(c.points.values())
    _usages(points, sources)

    by_path: dict[str, list] = defaultdict(list)
    for s in conn.execute("SELECT id, path, start_line, end_line FROM symbol").fetchall():
        by_path[s["path"]].append(s)

    def symbol_at(path: str, line: int) -> str | None:
        best = None
        for s in by_path.get(path, []):
            if s["start_line"] - 2 <= line <= s["end_line"] and (best is None or s["start_line"] >= best["start_line"]):
                best = s
        return best["id"] if best else None

    with ledger.tx(conn):
        conn.execute("DELETE FROM extension_point")
        for p in points:
            locs = [{"path": p["path"], "start_line": p["line"], "end_line": p["line"]}]
            locs += [{"path": i["path"], "start_line": i["line"], "end_line": i["line"]} for i in p["implementations"][:20]]
            try:
                ev = evidence.record(conn, project, provider="pattern", operation=f"extension_point:{p['kind']}",
                                     parameters={"name": p["name"]},
                                     result={"implementations": len(p["implementations"]), "usages": len(p["usages"])},
                                     locations=locs, confidence="inferred",
                                     limitations=[limitations[p["kind"]]] if p["kind"] in limitations else [])
                ev_id = ev["evidence_id"]
            except ToolError:
                ev_id = None
            conn.execute(
                "INSERT OR REPLACE INTO extension_point (id, kind, name, language, path, line, symbol_id, implementations, usages, note, evidence_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p["id"], p["kind"], p["name"], p["language"], p["path"], p["line"], symbol_at(p["path"], p["line"]),
                 ledger.dumps(p["implementations"]), ledger.dumps(p["usages"]), p["note"], ev_id))
        for d in c.dynamic:
            unknowns.record(conn, question=f"{d['path']}:{d['line']} 의 동적 로딩({d['call']})이 실제로 어떤 코드를 불러오는지",
                            domain="extension", scope=f"dynamic-load:{d['path']}:{d['line']}", severity="medium",
                            required_level=L3, origin="inventory",
                            recommended_probes=[{"operation": "oss_runtime_ingest", "rationale": "실행 커버리지로 실제 로딩 대상 확인"},
                                                {"operation": "agent-read", "rationale": "인자 값의 출처(설정·상수) 추적"}])
    counts: dict[str, int] = defaultdict(int)
    for p in points:
        counts[p["kind"]] += 1
    return {"count": len(points), "by_kind": dict(counts), "dynamic_loaders": len(c.dynamic)}


def list_(conn, kind: str | None = None, path: str | None = None, offset: int = 0, limit: int = 200) -> dict:
    where, args = [], []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if path:
        where.append("path LIKE ?")
        args.append(f"{path}%")
    sql = "FROM extension_point" + (" WHERE " + " AND ".join(where) if where else "")
    total = conn.execute(f"SELECT COUNT(*) {sql}", args).fetchone()[0]
    rows = ledger.rows_to_dicts(conn.execute(
        f"SELECT * {sql} ORDER BY CASE kind WHEN 'interface' THEN 0 WHEN 'registry' THEN 1 WHEN 'middleware' THEN 2 "
        f"WHEN 'overridable-bean' THEN 3 WHEN 'spi' THEN 4 ELSE 5 END, path, line LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall())
    nxt = offset + len(rows)
    return {"items": rows, "total": total, "next_offset": nxt if nxt < total else None,
            "note": "정규식 근사(L0). 커스텀 결정 전 구현체·등록 위치를 oss_lsp_references 로 확인"}
