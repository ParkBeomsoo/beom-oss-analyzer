"""라우터 객체·마운트 조합 — 라우트가 '어느 라우터 변수'에 등록됐고 그 라우터가 '어디에 어떤 접두로' 붙는지 따라간다.

지원 형태 (정규식 근사, 파일 단위):
  Python  app = FastAPI() / r = APIRouter(prefix="/x") / bp = Blueprint(..., url_prefix="/x")
          app.include_router(mod.router, prefix=P) / app.register_blueprint(bp, url_prefix=P)
          from pkg import mod / from pkg.mod import router as alias / import pkg.mod as mod
  JS·TS   const app = express() / const r = express.Router()
          app.use("/p", mw, childRouter) — childRouter 는 같은 파일 변수 또는 require/import 한 모듈의 export
  Go      r := gin.Default() / g := r.Group("/p")

접두 식이 리터럴이 아니면 resolve.Resolver 로 펼친다 (상수·연결). 라우터 객체를 못 찾은 라우트는
접두 없이 두고 미확인으로 남긴다 (호출자 책임).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from oss_analyzer.core.resolve import Resolver, Scope, Unresolved, balanced, literal, split_top

PY_ROOT = re.compile(r"^\s*(\w+)\s*(?::[^=]+)?=\s*(?:fastapi\.)?(?:FastAPI|Flask)\(", re.M)
PY_ROUTER = re.compile(r"^\s*(\w+)\s*(?::[^=]+)?=\s*(?:\w+\.)?(APIRouter|Blueprint)\(", re.M)
PY_INCLUDE = re.compile(r"(\w+)\.(include_router|register_blueprint)\(", re.M)
PY_FROM_IMPORT = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+(.+)$", re.M)
PY_IMPORT_AS = re.compile(r"^\s*import\s+([\w.]+)\s+as\s+(\w+)", re.M)
JS_ROOT = re.compile(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:express|fastify|Koa)\(\s*\)")
JS_ROUTER = re.compile(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:express\.)?Router\(\s*\)")
JS_USE = re.compile(r"\b(\w+)\.use\(")
JS_REQUIRE = re.compile(r"""\b(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*["'](\.{1,2}/[^"']+)["']\s*\)""")
JS_IMPORT = re.compile(r"""^\s*import\s+(\w+)\s+from\s+["'](\.{1,2}/[^"']+)["']""", re.M)
JS_EXPORT = re.compile(r"(?:module\.exports\s*=\s*|export\s+default\s+)(\w+)")
GO_ROOT = re.compile(r"\b(\w+)\s*:?=\s*gin\.(?:Default|New)\(\)")
GO_GROUP = re.compile(r"\b(\w+)\s*:?=\s*(\w+)\.Group\(")


@dataclass
class Router:
    file: str
    var: str
    own_prefix: list[str] = field(default_factory=lambda: [""])
    root: bool = False
    parents: list[tuple[str, str, list[str]]] = field(default_factory=list)  # (부모 파일, 부모 변수, 마운트 접두들)
    notes: list[str] = field(default_factory=list)


def _line(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


class MountResolver:
    def __init__(self, resolver: Resolver, sources: dict[str, str]) -> None:
        self.resolver = resolver
        self.sources = sources  # path → text
        self.routers: dict[tuple[str, str], Router] = {}
        self.exports: dict[str, str] = {}  # 파일 → export 한 변수
        self._pending_js: list = []  # (자식, 부모, 가정) — export 정보가 다 모인 뒤 연결

    # -- 식 평가
    def _eval(self, file: str, text: str, idx: int, expr: str) -> tuple[list[str], list[str]]:
        expr = expr.strip()
        lit = literal(expr)
        if lit is not None:
            return [lit], []
        try:
            vals = self.resolver.resolve(expr, Scope(file, _line(text, idx), self.resolver._func_start(file, _line(text, idx))))
            return [v.text for v in vals], sorted({a for v in vals for a in v.assumptions})
        except (Unresolved, re.error, IndexError, ValueError) as e:
            return [""], [f"접두 식 {expr[:60]} 을 해석하지 못함 ({e})"]

    def _kwarg(self, args: list[str], *names: str) -> str | None:
        for a in args:
            m = re.match(rf"^\s*({'|'.join(names)})\s*=\s*(.*)$", a, re.S)
            if m:
                return m.group(2)
        return None

    # -- 모듈 경로 해석
    def _py_module_file(self, module: str) -> str | None:
        base = module.replace(".", "/")
        for cand in (f"{base}.py", f"{base}/__init__.py"):
            if cand in self.sources:
                return cand
        # src 레이아웃
        for cand in (f"src/{base}.py", f"src/{base}/__init__.py"):
            if cand in self.sources:
                return cand
        return None

    def _js_module_file(self, from_file: str, spec: str) -> str | None:
        base = PurePosixPath(from_file).parent / spec
        norm = PurePosixPath(*[p for p in base.parts if p != "."]).as_posix()
        parts: list[str] = []
        for p in norm.split("/"):
            if p == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(p)
        base_s = "/".join(parts)
        for ext in ("", ".js", ".ts", ".mjs", ".cjs", ".tsx", "/index.js", "/index.ts"):
            if base_s + ext in self.sources:
                return base_s + ext
        return None

    # -- 수집
    def collect(self) -> None:
        for path, text in self.sources.items():
            suffix = PurePosixPath(path).suffix
            if suffix == ".py":
                self._collect_py(path, text)
            elif suffix in (".js", ".ts", ".mjs", ".cjs", ".tsx", ".jsx"):
                self._collect_js(path, text)
            elif suffix == ".go":
                self._collect_go(path, text)

    def _router(self, file: str, var: str) -> Router:
        return self.routers.setdefault((file, var), Router(file, var))

    def _collect_py(self, path: str, text: str) -> None:
        aliases: dict[str, tuple[str, str | None]] = {}  # 별칭 → (모듈, 속성)
        for m in PY_FROM_IMPORT.finditer(text):
            module = m.group(1)
            for item in split_top(m.group(2).strip("() ")):
                nm = re.match(r"^(\w+)(?:\s+as\s+(\w+))?$", item.strip())
                if nm:
                    aliases[nm.group(2) or nm.group(1)] = (module, nm.group(1))
        for m in PY_IMPORT_AS.finditer(text):
            aliases[m.group(2)] = (m.group(1), None)
        for m in PY_ROOT.finditer(text):
            r = self._router(path, m.group(1))
            r.root = True
        for m in PY_ROUTER.finditer(text):
            r = self._router(path, m.group(1))
            args = split_top(balanced(text, m.end() - 1)[0])
            expr = self._kwarg(args, "prefix", "url_prefix")
            if expr is not None:
                r.own_prefix, notes = self._eval(path, text, m.start(), expr)
                r.notes += notes
        for m in PY_INCLUDE.finditer(text):
            parent = m.group(1)
            args = split_top(balanced(text, m.end() - 1)[0])
            if not args:
                continue
            child_ref = args[0].strip()
            expr = self._kwarg(args, "prefix", "url_prefix")
            prefixes, notes = self._eval(path, text, m.start(), expr) if expr else ([""], [])
            child = self._py_child(path, child_ref, aliases)
            if child is None:
                continue
            self._router(*child).parents.append((path, parent, prefixes))
            self._router(*child).notes += notes

    def _py_child(self, path: str, ref: str, aliases: dict) -> tuple[str, str] | None:
        if "." in ref:  # orders.router → orders 모듈의 router
            mod_alias, attr = ref.rsplit(".", 1)
            if mod_alias in aliases:
                module, name = aliases[mod_alias]
                module = f"{module}.{name}" if name else module
                file = self._py_module_file(module)
                return (file, attr) if file else None
            return None
        if ref in aliases:  # from app.routers.orders import router as orders_router
            module, name = aliases[ref]
            file = self._py_module_file(module)
            return (file, name) if file and name else None
        return (path, ref)

    def _collect_js(self, path: str, text: str) -> None:
        requires = {m.group(1): m.group(2) for m in JS_REQUIRE.finditer(text)}
        requires.update({m.group(1): m.group(2) for m in JS_IMPORT.finditer(text)})
        for m in JS_EXPORT.finditer(text):
            self.exports[path] = m.group(1)
        for m in JS_ROOT.finditer(text):
            self._router(path, m.group(1)).root = True
        for m in JS_ROUTER.finditer(text):
            self._router(path, m.group(1))
        for m in JS_USE.finditer(text):
            parent = m.group(1)
            try:
                args = split_top(balanced(text, m.end() - 1)[0])
            except Unresolved:
                continue
            if len(args) < 2:
                continue
            prefixes, notes = self._eval(path, text, m.start(), args[0])
            target = args[-1].strip()
            if not re.fullmatch(r"\w+", target):
                continue
            if target in requires:
                child_file = self._js_module_file(path, requires[target])
                if child_file is None:
                    continue
                child = (child_file, None)
            else:
                child = (path, target)
            self._pending_js.append((child, (path, parent, prefixes), notes))

    def _collect_go(self, path: str, text: str) -> None:
        for m in GO_ROOT.finditer(text):
            self._router(path, m.group(1)).root = True
        for m in GO_GROUP.finditer(text):
            child, parent = m.group(1), m.group(2)
            args = split_top(balanced(text, m.end() - 1)[0])
            prefixes, notes = self._eval(path, text, m.start(), args[0]) if args else ([""], [])
            r = self._router(path, child)
            r.parents.append((path, parent, prefixes))
            r.notes += notes

    def finalize(self) -> None:
        """JS require 로 가져온 모듈의 라우터는 그 모듈이 export 한 변수로 연결."""
        for (child_file, child_var), parent, notes in self._pending_js:
            var = child_var or self.exports.get(child_file)
            if var is None:
                continue
            r = self._router(child_file, var)
            r.parents.append(parent)
            r.notes += notes
        self._pending_js = []

    # -- 조합
    def prefixes(self, file: str, var: str, depth: int = 0) -> tuple[list[str], list[str]]:
        """라우터 변수의 전체 접두 목록과 가정."""
        r = self.routers.get((file, var))
        if r is None:
            return [""], [f"{file} 의 라우터 객체 {var} 를 찾지 못해 접두 없음으로 가정"]
        if depth > 8:
            return [""], ["마운트 단계가 너무 깊음"]
        own = r.own_prefix
        if r.root or not r.parents:
            notes = list(r.notes) + ([] if r.root else [f"라우터 {var} 가 어디에도 마운트되지 않아 루트로 가정"])
            return own, notes
        out, notes = [], list(r.notes)
        for pfile, pvar, mount_prefixes in r.parents:
            parent_prefixes, pnotes = self.prefixes(pfile, pvar, depth + 1)
            notes += pnotes
            for pp in parent_prefixes:
                for mp in mount_prefixes:
                    for op in own:
                        out.append(pp + mp + op)
        return sorted(set(out)) or [""], sorted(set(notes))


def build(resolver: Resolver, sources: dict[str, str]) -> MountResolver:
    mr = MountResolver(resolver, sources)
    mr.collect()
    mr.finalize()
    return mr
