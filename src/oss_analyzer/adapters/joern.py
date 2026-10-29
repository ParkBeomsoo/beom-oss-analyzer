"""Joern 어댑터 — 데이터 흐름(L2). 외부 프로세스로만 실행하고, 결과는 우리 스크립트가 JSON 으로 직렬화한다.

- CPG 는 (프로젝트, 언어)마다 한 번 만들어 작업 공간에 캐시한다 (joern-parse 는 분석 대상 코드를 실행하지 않는다).
- 질의는 배치로 받아 joern 을 한 번만 띄운다 (JVM 기동이 수십 초).
- Joern 서버 모드는 샌드박스가 없어(공식 경고) 쓰지 않고 --script 모드만 쓴다.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from oss_analyzer.common.response import ToolError

# 우리 언어 이름 → joern-parse --language 값
FRONTEND = {
    "c": "newc", "cpp": "newc", "java": "javasrc", "javascript": "jssrc", "typescript": "jssrc",
    "python": "pythonsrc", "go": "golang", "kotlin": "kotlin", "php": "php", "ruby": "rubysrc",
    "swift": "swiftsrc", "csharp": "csharpsrc", "rust": "rust",
}

QUERY_SCRIPT = r'''
import io.shiftleft.semanticcpg.language.*
import io.joern.dataflowengineoss.language.*

// Windows 의 joern.bat 은 --param k=v 의 '=' 를 구분자로 잘라 버려서, 입력은 환경변수로 받는다
@main def exec() = {
  val cpgFile = sys.env("OA_CPG")
  val queriesFile = sys.env("OA_QUERIES")
  val outFile = sys.env("OA_OUT")
  importCpg(cpgFile)
  val queries = ujson.read(os.read(os.Path(queriesFile))).arr
  val results = queries.map { q =>
    val file = q("path").str
    val line = q("line").num.toInt
    val sinkRe = q("sink").str
    val maxFlows = q.obj.get("max_flows").map(_.num.toInt).getOrElse(20)
    // 함수: 그 줄을 포함하는 가장 안쪽 함수 (정의 줄 = 어노테이션·시그니처 어디든 허용)
    val inFile = cpg.method.filter(m => m.filename.replace('\\', '/').endsWith(file)).l
    val calls = cpg.call.filter(c => c.method.filename.replace('\\', '/').endsWith(file) && c.lineNumber.contains(line)).l
    val methods = if (calls.nonEmpty) List() else inFile
      .filter(m => m.lineNumber.exists(_ <= line + 3) && m.lineNumberEnd.exists(_ >= line))
      .sortBy(m => m.lineNumberEnd.getOrElse(0) - m.lineNumber.getOrElse(0)).take(1)
    val sources = methods.flatMap(_.parameter.l) ++ calls.flatMap(_.argument.l) ++ calls
    val sinks = cpg.call.name(sinkRe).argument.l
    val flows = if (sources.isEmpty || sinks.isEmpty) List() else sinks.reachableByFlows(sources).take(maxFlows).l
    ujson.Obj(
      "query" -> q,
      "sources" -> sources.size,
      "sinks" -> sinks.size,
      "flows" -> ujson.Arr(flows.map { p =>
        ujson.Arr(p.elements.map { e =>
          val loc = e.location
          ujson.Obj("file" -> loc.filename.replace('\\', '/'), "line" -> loc.lineNumber.map(_.toInt).getOrElse(-1),
                    "code" -> e.code.take(200), "method" -> loc.methodFullName)
        }*)
      }*)
    )
  }
  os.write.over(os.Path(outFile), ujson.write(ujson.Arr(results.toSeq*)))
}
'''


def find_java21() -> str | None:
    """JAVA_HOME 후보 — 환경변수 → ~/.jdks → Program Files 순."""
    explicit = os.environ.get("OSS_ANALYZER_JAVA_HOME")
    if explicit and Path(explicit).exists():
        return explicit
    candidates = []
    for base in (Path.home() / ".jdks", Path("C:/Program Files/Java"), Path("C:/Program Files/Microsoft"),
                 Path("C:/Program Files/Eclipse Adoptium"), Path("/usr/lib/jvm")):
        if base.exists():
            candidates.extend(p for p in base.iterdir() if p.is_dir())
    for p in sorted(candidates, key=lambda p: p.name, reverse=True):
        name = p.name.lower()
        if any(tag in name for tag in ("-21", "21.", "jdk21", "jdk-21", "ms-21", "-23", "23.", "-25", "25.")):
            if (p / "bin" / ("java.exe" if os.name == "nt" else "java")).exists():
                return str(p)
    return None


class Joern:
    def __init__(self, home: Path) -> None:
        self.dir = Path(os.environ.get("OSS_ANALYZER_JOERN", home / "engines" / "joern-cli"))
        suffix = ".bat" if os.name == "nt" else ""
        self.joern = self.dir / f"joern{suffix}"
        self.parse = self.dir / f"joern-parse{suffix}"
        self.java_home = find_java21()

    @property
    def available(self) -> bool:
        return self.joern.exists() and self.parse.exists() and self.java_home is not None

    def _env(self) -> dict:
        env = {**os.environ, "JAVA_HOME": self.java_home or "", "JAVA_OPTS": os.environ.get("OSS_ANALYZER_JOERN_JAVA_OPTS", "-Xmx6g")}
        env["PATH"] = str(Path(self.java_home or "") / "bin") + os.pathsep + env.get("PATH", "")
        return env

    def version(self) -> str | None:
        if not self.available:
            return None
        return f"joern-cli @ {self.dir} (JAVA_HOME={self.java_home})"

    def build_cpg(self, repo: Path, language: str, out: Path, timeout: int = 3600) -> Path:
        if not self.available:
            raise ToolError("capability_unavailable", "Joern 또는 JDK 21+ 가 없음", recovery="oss_doctor 안내 참고")
        frontend = FRONTEND.get(language)
        if not frontend:
            raise ToolError("capability_unavailable", f"Joern 프론트엔드가 없는 언어: {language}")
        out.parent.mkdir(parents=True, exist_ok=True)
        args = [str(self.parse), str(repo), "--language", frontend, "--output", str(out)]
        if frontend == "javasrc":
            # delombok 결과를 분석하면 줄 번호가 원본과 달라져 근거 위치가 틀어진다 → 타입 정보에만 사용
            args += ["--frontend-args", "--delombok-mode", "types-only"]
        proc = subprocess.run(
            args,
            capture_output=True, timeout=timeout, env=self._env(), cwd=str(out.parent),
        )
        if proc.returncode != 0 or not out.exists():
            raise ToolError("engine_error", f"joern-parse 실패: {(proc.stderr or proc.stdout).decode('utf-8', 'replace')[-1200:]}")
        return out

    def run_queries(self, cpg: Path, queries: list[dict], timeout: int = 3600) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "dataflow.sc"
            script.write_text(QUERY_SCRIPT, encoding="utf-8")
            qfile = Path(tmp) / "queries.json"
            qfile.write_text(json.dumps(queries), encoding="utf-8")
            out = Path(tmp) / "out.json"
            env = {**self._env(), "OA_CPG": cpg.as_posix(), "OA_QUERIES": qfile.as_posix(), "OA_OUT": out.as_posix()}
            proc = subprocess.run([str(self.joern), "--script", str(script)],
                                  capture_output=True, timeout=timeout, env=env, cwd=tmp)
            if proc.returncode != 0 or not out.exists():
                raise ToolError("engine_error", f"joern 질의 실패: {(proc.stderr or proc.stdout).decode('utf-8', 'replace')[-1500:]}")
            return json.loads(out.read_text(encoding="utf-8"))
