"""동적 값 해석 — 코드로 조립되는 문자열(라우트 경로 등)을 정적으로 펼친다.

다루는 형태 (Rust · JS/TS · Java · Python 공통 근사):
- 문자열 리터럴, format!/f-string/템플릿 리터럴/`+` 연결
- 지역 바인딩 (let/const/final), 파일·저장소 상수 (const/static/static final/export const)
- 함수 매개변수·반복 변수 → 호출하는 쪽 인자를 따라가 펼침 (vec![(경로, 핸들러), ...] 같은 표)
- 설정 조회 `get(cfg, "key", "기본값")`, 환경변수 `env::var("X").unwrap_or("d")` → `{config:key}` 자리표시 + 기본값 가정

해석은 근사이므로 결과마다 가정(assumptions)과 근거 위치(locs)를 함께 돌려준다.
기본값을 가정했거나 일부를 못 펼친 경우 호출자는 그 사실을 미확인으로 남겨야 한다.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_DEPTH = 6
MAX_VALUES = 300

STRING_RE = re.compile(r'^(?:r#*)?"((?:[^"\\]|\\.)*)"#*$|^\'((?:[^\'\\]|\\.)*)\'$|^`([^`$]*)`$')
WRAPPERS = [r"^&\s*(?:mut\s+)?", r"\.to_string\(\)$", r"\.as_str\(\)$", r"\.into\(\)$", r"\.to_owned\(\)$",
            r"\.clone\(\)$", r"\.as_ref\(\)$", r"\.trim\(\)$"]
CALL_WRAP = re.compile(r"^(?:String::from|str|String\.valueOf|std::string::String::from)\((.*)\)$", re.S)
IDENT = r"[A-Za-z_][A-Za-z0-9_]*"


@dataclass
class Val:
    text: str
    locs: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def join(self, other: "Val") -> "Val":
        sites = {**self.extra.get("sites", {}), **other.extra.get("sites", {})}
        return Val(self.text + other.text, self.locs + other.locs, self.assumptions + other.assumptions,
                   {**self.extra, **other.extra, "sites": sites})

    def compatible(self, other: "Val") -> bool:
        """같은 함수의 매개변수를 서로 다른 호출부에서 가져온 값끼리는 섞지 않는다."""
        mine, theirs = self.extra.get("sites", {}), other.extra.get("sites", {})
        return all(mine[k] == theirs[k] for k in mine.keys() & theirs.keys())


@dataclass
class Scope:
    path: str
    line: int  # 이 줄 이전의 바인딩만 본다
    func_start: int = 1


class Unresolved(Exception):
    pass


# ---------------------------------------------------------------- 텍스트 유틸

def split_top(text: str, sep: str = ",") -> list[str]:
    """괄호·문자열 깊이 0 에서만 자른다."""
    out, depth, buf, quote, i = [], 0, [], None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif depth == 0 and text.startswith(sep, i):
            out.append("".join(buf).strip())
            buf = []
            i += len(sep)
            continue
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail or out:
        out.append(tail)
    return [p for p in out if p != ""]


def balanced(text: str, start: int) -> tuple[str, int]:
    """text[start] 가 여는 괄호일 때 짝이 맞는 닫는 괄호까지의 내용과 끝 위치."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    opener = text[start]
    closer = pairs[opener]
    depth, quote, i = 0, None, start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i
        i += 1
    raise Unresolved("괄호 짝이 맞지 않음")


def strip_comments(text: str) -> str:
    """문자열 밖의 // ... , /* ... */ , (python) # ... 주석 제거."""
    out, i, quote = [], 0, None
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = len(text) if j == -1 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = len(text) if j == -1 else j + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def strip_wrappers(expr: str) -> str:
    prev = None
    expr = expr.strip()
    while prev != expr:
        prev = expr
        for pat in WRAPPERS:
            expr = re.sub(pat, "", expr).strip()
        m = CALL_WRAP.match(expr)
        if m:
            expr = m.group(1).strip()
        if expr.startswith("(") and expr.endswith(")"):
            try:
                inner, end = balanced(expr, 0)
            except Unresolved:
                continue
            if end == len(expr) - 1 and len(split_top(inner)) == 1:  # 단순 괄호 — 튜플은 유지
                expr = inner.strip()
    return expr


def literal(expr: str) -> str | None:
    m = STRING_RE.match(expr.strip())
    if not m:
        return None
    raw = next(g for g in m.groups() if g is not None)
    return raw.replace('\\"', '"').replace("\\'", "'")


def normalize_path(path: str) -> str:
    """경로 조합 후 // 정리. 자리표시 {config:...} 는 유지."""
    if not path:
        return path
    path = re.sub(r"(?<!:)/{2,}", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


# ---------------------------------------------------------------- 해석기

class Resolver:
    def __init__(self, repo: Path, language: str, functions: dict[str, list[dict]], callers, file_list: list[str]) -> None:
        """
        functions: path → [{name, start_line, end_line}] (구조 지도 심볼)
        callers: (path, func_name) → [(caller_path, call_line, evidence_loc)]  호출하는 쪽 찾기 함수
        """
        self.repo = repo
        self.language = language
        self.functions = functions
        self.callers = callers
        self.files = [f for f in file_list if Path(f).suffix in {".rs", ".java", ".kt", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".py", ".go"}]
        self._cache: dict[str, list[str]] = {}

    # -- 소스 접근
    def lines(self, path: str) -> list[str]:
        if path not in self._cache:
            self._cache[path] = (self.repo / path).read_text(encoding="utf-8", errors="replace").splitlines()
        return self._cache[path]

    def text_from(self, path: str, line: int, max_lines: int = 60) -> str:
        return "\n".join(self.lines(path)[line - 1:line - 1 + max_lines])

    def enclosing_function(self, path: str, line: int) -> dict | None:
        best = None
        for fn in self.functions.get(path, []):
            if fn["start_line"] <= line <= fn["end_line"]:
                if best is None or fn["start_line"] >= best["start_line"]:
                    best = fn
        return best

    # -- 진입점
    def resolve(self, expr: str, scope: Scope, depth: int = 0) -> list[Val]:
        if depth > MAX_DEPTH:
            raise Unresolved("추적 깊이 초과")
        expr = strip_wrappers(expr)
        lit = literal(expr)
        if lit is not None:
            return [Val(lit)]
        for handler in (self._format, self._template, self._concat, self._config_call, self._env, self._field, self._ident):
            out = handler(expr, scope, depth)
            if out is not None:
                return out[:MAX_VALUES]
        raise Unresolved(f"해석할 수 없는 식: {expr[:80]}")

    def _product(self, parts: list[list[Val]]) -> list[Val]:
        out = []
        for combo in itertools.product(*parts):
            v = Val("")
            ok = True
            for p in combo:
                if not v.compatible(p):
                    ok = False
                    break
                v = v.join(p)
            if ok:
                out.append(v)
                if len(out) >= MAX_VALUES:
                    break
        return out

    def _format(self, expr: str, scope: Scope, depth: int) -> list[Val] | None:
        m = re.match(r"^format!\s*\(", expr)
        if not m:
            return None
        inner, _ = balanced(expr, expr.index("("))
        args = split_top(inner)
        tpl = literal(args[0]) if args else None
        if tpl is None:
            raise Unresolved("format! 의 첫 인자가 리터럴이 아님")
        positional = args[1:]
        named = {}
        for a in positional:
            if re.match(rf"^{IDENT}\s*=[^=]", a):
                k, v = a.split("=", 1)
                named[k.strip()] = v.strip()
        positional = [a for a in positional if not re.match(rf"^{IDENT}\s*=[^=]", a)]
        parts: list[list[Val]] = []
        idx = 0
        for piece in re.split(r"(\{[^{}]*\})", tpl.replace("{{", "\0").replace("}}", "\1")):
            if not piece:
                continue
            if piece.startswith("{") and piece.endswith("}"):
                key = piece[1:-1].split(":")[0]
                if key == "":
                    src = positional[idx]
                    idx += 1
                elif key.isdigit():
                    src = positional[int(key)]
                elif key in named:
                    src = named[key]
                else:
                    src = key  # format!("{name}") — 같은 이름 지역 변수
                parts.append(self.resolve(src, scope, depth + 1))
            else:
                parts.append([Val(piece.replace("\0", "{").replace("\1", "}"))])
        return self._product(parts)

    def _template(self, expr: str, scope: Scope, depth: int) -> list[Val] | None:
        if not (expr.startswith("`") and expr.endswith("`")) and not re.match(r'^f["\']', expr):
            return None
        body = expr[1:-1] if expr.startswith("`") else expr[2:-1]
        pattern = r"(\$\{[^}]*\})" if expr.startswith("`") else r"(\{[^}]*\})"
        parts = []
        for piece in re.split(pattern, body):
            if not piece:
                continue
            if re.fullmatch(pattern, piece):
                inner = piece[2:-1] if piece.startswith("$") else piece[1:-1]
                parts.append(self.resolve(inner, scope, depth + 1))
            else:
                parts.append([Val(piece)])
        return self._product(parts)

    def _concat(self, expr: str, scope: Scope, depth: int) -> list[Val] | None:
        pieces = split_top(expr, "+")
        if len(pieces) < 2:
            return None
        return self._product([self.resolve(p, scope, depth + 1) for p in pieces])

    def _config_call(self, expr: str, scope: Scope, depth: int) -> list[Val] | None:
        """get_str_value(&cfg, "key", "default") 같은 설정 조회 — 마지막 문자열 리터럴을 기본값으로 가정."""
        m = re.match(rf"^({IDENT}(?:(?:::|\.){IDENT})*)\s*\(", expr)
        if not m or not expr.endswith(")"):
            return None
        inner, end = balanced(expr, expr.index("("))
        if end != len(expr) - 1:
            return None
        lits = [literal(a) for a in split_top(inner)]
        lits = [x for x in lits if x is not None]
        if len(lits) < 2:
            return None
        key, default = lits[0], lits[-1]
        return [Val(default, assumptions=[f"설정 {key} 는 기본값 '{default}' 로 가정 ({m.group(1)})"])]

    def _env(self, expr: str, scope: Scope, depth: int) -> list[Val] | None:
        m = re.match(r"""^(?:std::)?env::var\(\s*"([^"]+)"\s*\).*unwrap_or(?:_else)?\(\s*(?:\|\|\s*)?"([^"]*)".*$""", expr, re.S) \
            or re.match(r"""^process\.env\.(\w+)\s*(?:\?\?|\|\|)\s*["'`]([^"'`]*)["'`]$""", expr) \
            or re.match(r"""^os\.(?:environ\.get|getenv)\(\s*["'](\w+)["']\s*,\s*["']([^"']*)["']\s*\)$""", expr)
        if m:
            return [Val(m.group(2), assumptions=[f"환경변수 {m.group(1)} 는 기본값 '{m.group(2)}' 로 가정"])]
        if re.match(r"^(?:process\.env\.\w+|System\.getenv\(|(?:std::)?env::var\()", expr):
            raise Unresolved(f"기본값 없는 환경변수: {expr[:60]}")
        return None

    def _field(self, expr: str, scope: Scope, depth: int) -> list[Val] | None:
        """obj.field / Type::CONST / Class.CONST → 저장소에서 같은 이름의 바인딩·상수를 찾는다 (유일할 때만)."""
        m = re.match(rf"^(?:{IDENT}(?:\.|::))+({IDENT})$", expr)
        if not m:
            return None
        name = m.group(1)
        found = self._repo_binding(name)
        if found is None:
            raise Unresolved(f"{expr} 의 값을 저장소에서 찾지 못함")
        path, line, rhs = found
        vals = self.resolve(rhs, Scope(path, line + 1, self._func_start(path, line)), depth + 1)
        loc = {"path": path, "start_line": line, "end_line": line}
        return [Val(v.text, v.locs + [loc], v.assumptions + [f"{expr} 를 {path}:{line} 의 {name} 로 대응 (이름 기반)"], v.extra) for v in vals]

    def _ident(self, expr: str, scope: Scope, depth: int) -> list[Val] | None:
        if not re.fullmatch(IDENT, expr):
            return None
        # 1) 지역 바인딩
        local = self._local_binding(expr, scope)
        if local is not None:
            line, rhs = local
            vals = self.resolve(rhs, Scope(scope.path, line, scope.func_start), depth + 1)
            loc = {"path": scope.path, "start_line": line, "end_line": line}
            return [Val(v.text, v.locs + [loc], v.assumptions, v.extra) for v in vals]
        # 2) 반복 변수 / 매개변수 → 호출하는 쪽
        loop = self._loop_binding(expr, scope)
        if loop is not None:
            return self._expand_loop(expr, scope, loop, depth)
        fn = self.enclosing_function(scope.path, scope.line)
        if fn is not None:
            params = self._params(scope.path, fn)
            if expr in params:
                return self._expand_param(scope.path, fn, params.index(expr), depth, element=None)
        # 3) 파일·저장소 상수
        found = self._repo_binding(expr, prefer=scope.path)
        if found is not None:
            path, line, rhs = found
            vals = self.resolve(rhs, Scope(path, line + 1, self._func_start(path, line)), depth + 1)
            loc = {"path": path, "start_line": line, "end_line": line}
            return [Val(v.text, v.locs + [loc], v.assumptions, v.extra) for v in vals]
        raise Unresolved(f"{expr} 의 정의를 찾지 못함")

    # -- 바인딩 찾기
    def _func_start(self, path: str, line: int) -> int:
        fn = self.enclosing_function(path, line)
        return fn["start_line"] if fn else 1

    def _local_binding(self, name: str, scope: Scope) -> tuple[int, str] | None:
        if not re.fullmatch(IDENT, name):
            return None
        lines = self.lines(scope.path)
        # let/const/var/final 선언 + Python·Go 의 맨 대입문(NAME = ..., NAME := ...)
        pat = re.compile(rf"(?:\blet\s+(?:mut\s+)?|\bconst\s+|\bvar\s+|\bfinal\s+\w[\w<>\[\], ]*\s+|\b(?:String|var)\s+|^\s*){name}\b\s*(?::(?!=)[^=]+)?:?=(?!=)\s*(.*)$")
        for ln in range(scope.line - 1, scope.func_start - 1, -1):
            if ln < 1:
                break
            m = pat.search(lines[ln - 1])
            if m:
                return ln, self._statement(scope.path, ln, m.group(1))
        return None

    def _statement(self, path: str, line: int, first: str) -> str:
        """우변이 여러 줄에 걸치면 ; 또는 괄호가 닫힐 때까지 이어 붙인다."""
        text = first
        ln = line
        while True:
            stripped = text.rstrip()
            depth = sum(stripped.count(c) for c in "([{") - sum(stripped.count(c) for c in ")]}")
            if depth <= 0 and (stripped.endswith(";") or not stripped.endswith((",", "+", "(", "[", "{"))):
                break
            ln += 1
            if ln > len(self.lines(path)) or ln - line > 80:
                break
            text += "\n" + self.lines(path)[ln - 1]
        return text.rstrip().rstrip(";").strip()

    def _loop_binding(self, name: str, scope: Scope) -> tuple[int, str, int | None] | None:
        """for (a, b) in X / for (const [a, b] of X) / for (T a : X) → (줄, X, 튜플 위치)"""
        lines = self.lines(scope.path)
        for ln in range(scope.line, scope.func_start - 1, -1):
            if ln < 1:
                break
            text = lines[ln - 1]
            m = re.search(r"\bfor\s*\(?\s*(?:const|let|var|final\s+\w+|\w+(?:<[^>]*>)?)?\s*(\(.*?\)|\[.*?\]|\w+)\s*(?:\bin\b|\bof\b|:)\s*(.+?)\s*\)?\s*\{?\s*$", text)
            if not m:
                continue
            pattern, iterable = m.group(1), m.group(2)
            names = [n.strip() for n in pattern.strip("()[]").split(",")]
            if name in names:
                return ln, strip_wrappers(iterable), (names.index(name) if len(names) > 1 else None)
        return None

    def _expand_loop(self, name: str, scope: Scope, loop: tuple[int, str, int | None], depth: int) -> list[Val]:
        line, iterable, position = loop
        fn = self.enclosing_function(scope.path, scope.line)
        params = self._params(scope.path, fn) if fn else []
        if iterable in params:
            return self._expand_param(scope.path, fn, params.index(iterable), depth, element=position, loop_line=line)
        local = (self._local_binding(iterable, Scope(scope.path, line, scope.func_start))
                 if re.fullmatch(IDENT, iterable) else None)
        if local is not None:
            return self._elements(local[1], position, Scope(scope.path, local[0], scope.func_start), depth,
                                  {"path": scope.path, "start_line": local[0], "end_line": local[0]})
        return self._elements(iterable, position, scope, depth, None)

    def _elements(self, collection: str, position: int | None, scope: Scope, depth: int, loc: dict | None) -> list[Val]:
        """vec![(a, h), ...] / [a, b] / &[...] 의 원소(튜플이면 position 번째)를 펼친다. extra.siblings 에 튜플 나머지."""
        collection = strip_wrappers(strip_comments(collection))
        collection = re.sub(r"^vec!\s*", "", collection)
        collection = re.sub(r"^(?:List\.of|Arrays\.asList|Set\.of)\s*", "", collection)
        if not collection or collection[0] not in "[({":
            raise Unresolved(f"펼칠 수 없는 컬렉션: {collection[:60]}")
        inner, _ = balanced(collection, 0)
        out = []
        for item in split_top(inner):
            item = item.strip()
            if position is not None:
                if item[0] not in "([":
                    raise Unresolved("튜플이 아닌 원소")
                parts = split_top(balanced(item, 0)[0])
                target = parts[position]
                siblings = [p for i, p in enumerate(parts) if i != position]
            else:
                target, siblings = item, []
            for v in self.resolve(target, scope, depth + 1):
                v.extra = {**v.extra, "siblings": siblings, "item_text": target.strip()}
                if loc:
                    v.locs = v.locs + [loc]
                out.append(v)
        return out

    def _params(self, path: str, fn: dict) -> list[str]:
        text = self.text_from(path, fn["start_line"], 30)
        m = re.search(r"\(", text)
        if not m:
            return []
        try:
            inner, _ = balanced(text, m.start())
        except Unresolved:
            return []
        names = []
        for p in split_top(inner):
            p = p.strip()
            if p in ("self", "&self", "&mut self", "this"):
                continue
            rust = re.match(rf"^(?:mut\s+)?({IDENT})\s*:", p)
            jvm = re.match(rf"^(?:final\s+)?(?:@\w+\s+)*[\w.<>\[\], ?]+\s+({IDENT})$", p)
            js = re.match(rf"^({IDENT})\s*(?:[:=?].*)?$", p)
            m2 = rust or jvm or js
            names.append(m2.group(1) if m2 else p)
        return names

    def call_sites(self, path: str, name: str) -> list[tuple[str, int, dict]]:
        """호출하는 함수 본문 전체에서 `name(` 호출부를 모두 찾는다.

        구조 지도·LSP 는 (호출자, 피호출자) 쌍마다 간선 1개만 주는 경우가 있어,
        호출자 함수를 알아낸 뒤 그 본문을 직접 훑어 호출 지점을 전부 모은다.
        """
        seeds = self.callers(path, name)
        regions: dict[tuple[str, int, int], dict] = {}
        for caller_path, call_line, loc in seeds:
            fn = self.enclosing_function(caller_path, call_line)
            start, end = (fn["start_line"], fn["end_line"]) if fn else (call_line, call_line)
            regions[(caller_path, start, end)] = loc
        sites = []
        call_re = re.compile(rf"(?<![\w.:])(?:self\.|Self::|super::|crate::[\w:]*::)?{re.escape(name)}\s*\(")
        for (caller_path, start, end) in sorted(regions):
            lines = self.lines(caller_path)
            for ln in range(start, min(end, len(lines)) + 1):
                code = strip_comments(lines[ln - 1])
                if call_re.search(code) and not re.search(rf"\bfn\s+{re.escape(name)}\b|\bdef\s+{re.escape(name)}\b|function\s+{re.escape(name)}\b", code):
                    sites.append((caller_path, ln, {"path": caller_path, "start_line": ln, "end_line": ln}))
        return sites

    def _expand_param(self, path: str, fn: dict, index: int, depth: int, element: int | None, loop_line: int | None = None) -> list[Val]:
        sites = self.call_sites(path, fn["name"])
        if not sites:
            raise Unresolved(f"{fn['name']} 을 호출하는 곳을 찾지 못함")
        out: list[Val] = []
        failures = []
        for caller_path, call_line, call_loc in sites:
            try:
                text = strip_comments(self.text_from(caller_path, call_line, 200))
                m = re.search(rf"\b{re.escape(fn['name'])}\s*\(", text)
                if not m:
                    continue
                args_text, _ = balanced(text, m.end() - 1)
                args = split_top(args_text)
                if index >= len(args):
                    continue
                scope = Scope(caller_path, call_line, self._func_start(caller_path, call_line))
                if element is None:
                    vals = self.resolve(args[index], scope, depth + 1)
                else:
                    vals = self._elements(args[index], element, scope, depth, None)
            except Unresolved as e:
                failures.append(f"{caller_path}:{call_line} ({e})")
                continue
            site_key = f"{path}::{fn['name']}"
            caller_lines = self.lines(caller_path)
            for v in vals:
                item = v.extra.get("item_text")
                if item and "item_loc" not in v.extra:
                    for ln in range(call_line, min(call_line + 200, len(caller_lines)) + 1):
                        if item in caller_lines[ln - 1]:
                            v.extra["item_loc"] = {"path": caller_path, "start_line": ln, "end_line": ln}
                            v.locs = [v.extra["item_loc"]] + v.locs
                            break
                v.locs = v.locs + [call_loc]
                v.extra = {**v.extra, "sites": {**v.extra.get("sites", {}), site_key: f"{caller_path}:{call_line}"}}
                out.append(v)
        if not out:
            raise Unresolved(f"{fn['name']} 호출부 인자를 읽지 못함: {'; '.join(failures)[:300]}")
        if failures:
            for v in out:
                v.extra.setdefault("partial_failures", []).extend(failures)
        return out

    def _repo_binding(self, name: str, prefer: str | None = None) -> tuple[str, int, str] | None:
        if not re.fullmatch(IDENT, name):
            return None
        pat = re.compile(
            rf"(?:\b(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+{name}\s*:[^=]+="
            rf"|\b(?:public|private|protected)?\s*static\s+final\s+\w+\s+{name}\s*="
            rf"|\b(?:export\s+)?const\s+{name}\s*(?::[^=]+)?="
            rf"|\blet\s+(?:mut\s+)?{name}\s*(?::[^=]+)?="
            rf"|^{name}\s*(?::[^=]+)?=(?!=))\s*(.*)$"  # 모듈 최상위 맨 대입 (Python 상수 등)
        )
        hits = []
        files = ([prefer] if prefer else []) + [f for f in self.files if f != prefer]
        for path in files:
            try:
                lines = self.lines(path)
            except OSError:
                continue
            for i, text in enumerate(lines, start=1):
                m = pat.search(text)
                if m:
                    rhs = self._statement(path, i, m.group(1))
                    if re.search(rf"(?:\.|::){name}(?:\.clone\(\))?$", rhs):  # let x = cfg.x; 같은 자기 참조는 후보 아님
                        continue
                    hits.append((path, i, rhs))
            if prefer and path == prefer and hits:
                break
        return hits[0] if len(hits) == 1 or (hits and prefer and hits[0][0] == prefer) else None
