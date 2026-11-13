"""화면(웹 기술) ↔ 툴킷 연결 — Qt WebChannel 로 같은 프로세스 안에서만 통한다. 네트워크 포트를 열지 않는다.

모든 슬롯은 JSON 문자열을 받고 돌려준다: {"ok": true, "data": …} 또는 {"ok": false, "error": "…"}.
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import re
import shutil
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from oss_analyzer import __version__
from oss_analyzer.app import view
from oss_analyzer.common.response import ToolError
from oss_analyzer.core import agents, inbox, inventory, port, project, runner
from oss_analyzer.core import port_guard as guard
from oss_analyzer.core.checkpoints import Checkpoints


def _ok(data=None) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False, default=str)


def _err(e: Exception) -> str:
    msg = getattr(e, "message", None) or f"{type(e).__name__}: {e}"
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def safe(fn):
    def wrap(self, *a):
        try:
            return _ok(fn(self, *a))
        except Exception as e:  # noqa: BLE001 — 화면에는 오류 문장만
            return _err(e)
    wrap.__name__ = fn.__name__
    return wrap


class Api(QObject):
    created = Signal(str)       # JSON {"run_id"} 또는 {"error"}
    notify = Signal(str, str)   # 제목, 내용 — 창 밖 알림
    agentsChanged = Signal(str)  # 에이전트 설치·로그인 상태 (뒤에서 확인이 끝났을 때)

    def __init__(self, settings, window=None):
        super().__init__()
        self.settings = settings
        self.window = window
        self.runners: dict[str, runner.Runner] = {}
        self.threads: dict[str, threading.Thread] = {}
        self._last_status: dict[str, str] = {}

    # -- 읽기
    @Slot(result=str)
    @safe
    def bootstrap(self):
        self._refresh_agents()
        return {"version": __version__, "agents": self._agents_now(), "runs": view.runs(self.settings),
                "defaults": self._prefs().get("defaults") or {},
                "kinds": inbox.KINDS, "default_policy": inbox.DEFAULT_POLICY,
                "permission_modes": [{"id": k, "label": m["label"], "desc": m["desc"], "policy": m["policy"]}
                                     for k, m in inbox.PERMISSION_MODES.items()]}

    @Slot(result=str)
    @safe
    def listRuns(self):
        rows = view.runs(self.settings)
        for r in rows:
            prev = self._last_status.get(r["id"])
            if prev and prev != r["status"] and r["status"] in ("내 차례", "완료", "실패", "확인 필요"):
                self.notify.emit(r["name"] or "프로젝트", f"{r['status']}")
            self._last_status[r["id"]] = r["status"]
        return rows

    @Slot(str, result=str)
    @safe
    def runDetail(self, run_id):
        d = view.detail(self.settings, run_id)
        d["running"] = self._busy(run_id)
        return d

    @Slot(str, result=str)
    @safe
    def changes(self, run_id):
        return view.changes(self.settings, run_id)

    @Slot(result=str)
    @safe
    def agentStatus(self):
        """화면은 바로 받는다 (마지막으로 확인한 값). 1분이 지났으면 뒤에서 다시 확인하고 agentsChanged 로 알린다."""
        if time.monotonic() - getattr(self, "_agents_at", 0) > 60:
            self._refresh_agents()
        return self._agents_now()

    def _agents_cache_file(self) -> Path:
        from oss_analyzer.core import storage
        return storage.cache_dir(self.settings) / "agents.json"

    def _agents_now(self) -> list:
        if getattr(self, "_agents", None):
            return self._agents
        try:  # 지난번 앱에서 확인한 값 — 시작을 몇 초 늦추지 않게
            cached = json.loads(self._agents_cache_file().read_text(encoding="utf-8"))
            if isinstance(cached, list) and cached:
                return cached
        except (OSError, ValueError):
            pass
        if not getattr(self, "_agents_quick", None):
            self._agents_quick = agents.statuses(check=False)
        return self._agents_quick

    def _refresh_agents(self) -> None:
        if getattr(self, "_agents_busy", False):
            return
        self._agents_busy = True

        def work():
            try:
                data = agents.statuses()
                self._agents, self._agents_at = data, time.monotonic()
                try:
                    self._agents_cache_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass
                self.agentsChanged.emit(json.dumps(data, ensure_ascii=False))
            finally:
                self._agents_busy = False

        threading.Thread(target=work, daemon=True).start()

    # -- 만들기·실행
    @Slot(str, result=str)
    @safe
    def createRun(self, payload):
        v = json.loads(payload)
        if v.get("mode") == "plan" and not v.get("target_root"):
            # 기획만 할 때는 폴더를 안 골라도 된다 — 문서 폴더 아래 (툴킷 홈은 에이전트가 쓸 수 없는 곳이라 안 됨)
            slug = re.sub(r"[^\w가-힣-]+", "-", (v.get("request") or "plan").strip()[:30]).strip("-") or "plan"
            docs_dir = Path.home() / "Documents"
            base = (docs_dir if docs_dir.is_dir() else Path.home()) / "beom-oss-analyzer" / "기획"
            v["target_root"] = str(base / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}")
        if not v.get("target_root"):
            raise ToolError("invalid_request", "작업할 폴더를 정해 주세요")
        if v["mode"] in ("plan", "free") and not (v.get("request") or "").strip() and not v.get("attachments"):
            raise ToolError("invalid_request", "무엇을 할지 적어 주세요")
        if v["mode"] == "request" and not v.get("request", "").strip() and not v.get("attachments") and not v.get("urls"):
            raise ToolError("invalid_request", "만들 기능을 설명하거나 기획서를 첨부해 주세요")
        if v["mode"] == "oss" and not v.get("source", "").strip():
            raise ToolError("invalid_request", "원본 저장소를 입력해 주세요")
        if v.get("auth") == "api_key":
            names = agents.get(v.get("agent", "claude")).api_key_env
            if names and not any(os.environ.get(n) for n in names):
                raise ToolError("not_ready", f"API 키 인증을 골랐지만 환경 변수({' 또는 '.join(names)})가 없습니다",
                                recovery="키를 환경 변수로 넣고 앱을 다시 열거나, 인증을 '내 로그인'으로 두세요")
        threading.Thread(target=self._prepare, args=(v,), daemon=True).start()
        try:  # 다음 새 작업은 이번 선택으로 시작 (Claude 고정이 아니라 쓰던 에이전트)
            from oss_analyzer.core import storage
            storage.save_prefs(self.settings, {"defaults": {k: v.get(k) for k in (
                "agent", "model", "effort", "policy", "max_rounds", "plan_approval", "confirm_at", "auth") if k in v}})
        except Exception:  # noqa: BLE001 — 기본값 저장 실패가 실행을 막지 않게
            pass
        return {"preparing": True}

    def _prepare(self, v: dict) -> None:
        try:
            v = {**v, "request": self._attach(v)}
            context = None
            if v.get("context_dir"):
                opened = project.open_repo(self.settings, v["context_dir"])
                conn, proj = project.load(self.settings, opened["project"]["id"])
                if not proj.get("inventory_at"):
                    inventory.build(conn, self.settings, proj, False)
                context = proj["id"]
            run = runner.create(self.settings, mode=v["mode"], target_root=v["target_root"], request=v.get("request", ""),
                                name=v.get("name") or None, source=v.get("source"), feature=v.get("feature"),
                                source_run=v.get("source_run"), context_project=context, max_rounds=int(v.get("max_rounds") or 0),
                                model=v.get("model") or None, auth=v.get("auth", "subscription"), policy=v.get("policy"),
                                agent=v.get("agent", "claude"), plan_approval=bool(v.get("plan_approval", True)),
                                effort=v.get("effort") or None, confirm_at=v.get("confirm_at") or "start")
            if v["mode"] == "oss":
                conn, proj = project.load(self.settings, run["project_id"])
                if not proj.get("inventory_at"):
                    inventory.build(conn, self.settings, proj, False)
            for note in getattr(self, "_attach_notes", []):
                inbox.log(self.settings, run["id"], "note", text=note, tone="warn")
            self.created.emit(json.dumps({"run_id": run["id"], "start": bool(v.get("start", True))}))
        except Exception as e:  # noqa: BLE001
            self.created.emit(json.dumps({"error": getattr(e, "message", None) or f"{type(e).__name__}: {e}"}, ensure_ascii=False))

    def _attach(self, v: dict) -> str:
        """첨부한 기획서·문서·링크를 대상 폴더의 .oss/brief/ 에 두고 요청에 '먼저 읽으라'고 적는다 (에이전트 샌드박스 안).

        docx·pptx·xlsx·hwpx 는 글로 바꾼 .md 를 함께 둔다. 링크는 공개 페이지면 글로 저장하고,
        로그인이 필요하면 링크만 남겨 에이전트가 웹으로 읽게 한다 (웹 권한 규칙을 따름).
        """
        from oss_analyzer.core import docs

        files = [Path(f) for f in v.get("attachments") or [] if f and not re.match(r"https?://", str(f))]
        urls = [str(f).strip() for f in (v.get("attachments") or []) + (v.get("urls") or []) if re.match(r"https?://", str(f))]
        request = (v.get("request") or "").strip()
        self._attach_notes = []
        if not files and not urls:
            return request
        brief = Path(v["target_root"]) / ".oss" / "brief"
        brief.mkdir(parents=True, exist_ok=True)
        names, links = [], []
        for f in files:
            if not f.is_file():
                raise ToolError("not_found", f"첨부 파일이 없습니다: {f}")
            if f.stat().st_size > 20 * 1024 * 1024:
                raise ToolError("invalid_request", f"첨부 파일이 너무 큽니다 (20MB 까지): {f.name}")
            shutil.copy2(f, brief / f.name)
            text = docs.to_text(f)
            if text:
                md = brief / f"{f.name}.md"  # spec.docx → spec.docx.md (spec.md 나 기획 결과 기획서.md 와 겹치지 않게)
                md.write_text(f"<!-- {f.name} 에서 뽑은 글 -->\n\n{text}", encoding="utf-8")
                names.append(f".oss/brief/{md.name} (원본 {f.name})")
            else:
                names.append(f".oss/brief/{f.name}")
        for i, url in enumerate(dict.fromkeys(urls), 1):
            got = docs.fetch_url(url)
            if got.get("ok"):
                slug = re.sub(r"[^\w가-힣-]+", "-", got["title"])[:50].strip("-") or f"link-{i}"
                md = brief / f"link-{slug}.md"
                n = 2
                while md.exists():
                    md = brief / f"link-{slug}-{n}.md"
                    n += 1
                md.write_text(f"<!-- 출처: {url} -->\n\n# {got['title']}\n\n{got['text']}", encoding="utf-8")
                names.append(f".oss/brief/{md.name} (출처 {url})")
            else:
                links.append(url)
                why = "로그인이 필요한 페이지" if got.get("needs_login") else got.get("error", "가져오지 못함")
                self._attach_notes.append(f"{url} — {why}라 내용을 가져오지 못했습니다. 에이전트가 웹으로 읽으려 하면 권한을 묻습니다 "
                                          "(로그인이 필요한 문서는 내보내서 파일로 첨부하면 확실합니다).")
        head = request or "첨부한 기획서대로 만든다."
        out = head + "\n\n[첨부 문서 — 먼저 모두 읽고 요구사항·합격 기준에 반영한다. 문서와 요청이 다르면 묻는다]\n" + "\n".join(f"- {n}" for n in names)
        if links:
            out += "\n[링크 — 내용을 미리 받지 못했다. 웹으로 읽을 수 있으면 읽고, 못 읽으면 사용자에게 내용을 요청한다]\n" + "\n".join(f"- {u}" for u in links)
        return out

    @Slot(str, result=str)
    @safe
    def screenshot(self, path):
        """검증이 남긴 스크린샷을 화면에 — 툴킷 홈 안의 png 만 (data: 주소로)."""
        import base64
        p = Path(path).resolve()
        if p.suffix.lower() != ".png" or not inbox._inside(str(p), self.settings.home) or not p.is_file():
            raise ToolError("invalid_request", "보여 줄 수 없는 파일")
        if p.stat().st_size > 8 * 1024 * 1024:
            raise ToolError("invalid_request", "스크린샷이 너무 큽니다")
        return {"url": "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()}

    @Slot(str, result=str)
    @safe
    def readBrief(self, run_id):
        """기획 모드의 결과 문서 (.oss/brief/기획서.md) — 없으면 가장 최근에 바뀐 .md."""
        run = inbox.load_run(self.settings, run_id)
        brief = Path(run["target_root"]) / ".oss" / "brief"
        main = brief / "기획서.md"
        if not main.is_file():
            mds = sorted(brief.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if brief.is_dir() else []
            main = mds[0] if mds else None
        if not main:
            return {"path": None, "text": ""}
        return {"path": str(main), "text": main.read_text(encoding="utf-8", errors="replace")[:400_000]}

    @Slot(result=str)
    @safe
    def pickFiles(self):
        files, _ = QFileDialog.getOpenFileNames(None, "기획서·문서 첨부", str(Path.home()),
                                                "문서 (*.md *.txt *.pdf *.docx *.pptx *.xlsx *.hwpx *.json *.yaml *.yml *.csv *.png *.jpg);;모든 파일 (*)")
        return files

    def _busy(self, run_id) -> bool:
        """이 창의 스레드든 다른 곳(CLI·다른 창)이든 지금 이 실행을 진행 중인가."""
        t = self.threads.get(run_id)
        return bool(t and t.is_alive()) or runner.lock_owner(self.settings, run_id) is not None

    @Slot(str, result=str)
    @safe
    def start(self, run_id):
        if self._busy(run_id):
            return {"running": True}

        def more_rounds(x):  # 사용자가 이어서 진행을 눌렀다 — 라운드를 다 썼으면 몇 개 더 준다 (0 = 제한 없음)
            if int(x.get("round") or 0) >= runner.HARD_ROUNDS:
                x["round"] = 0  # 안전 상한에 걸렸던 실행 — 사람이 이어 가기로 했다
            if x.get("max_rounds") and int(x.get("round") or 0) >= int(x["max_rounds"]):
                x["max_rounds"] = int(x.get("round") or 0) + 4

        inbox.update_run(self.settings, run_id, more_rounds)
        r = runner.Runner(self.settings, run_id)
        st = r.agent.status()
        if not st.get("ready") and r.agent.id != "gemini":
            raise ToolError("not_ready", st.get("hint") or f"{r.agent.name} 준비가 필요합니다")
        self.runners[run_id] = r

        def work():
            try:
                r.run()
            except ToolError as e:
                if e.code == "conflict":  # 다른 창·CLI 가 이미 진행 중 — 그 실행의 상태는 건드리지 않는다
                    inbox.log(self.settings, run_id, "note", text=e.message)
                    return
                inbox.log(self.settings, run_id, "error", message=e.message, trace=traceback.format_exc()[-1500:])
                inbox.update_run(self.settings, run_id, lambda x: x.update(status="error"))
            except Exception as e:  # noqa: BLE001
                inbox.log(self.settings, run_id, "error", message=f"{type(e).__name__}: {getattr(e, 'message', e)}",
                          trace=traceback.format_exc()[-1500:])
                inbox.update_run(self.settings, run_id, lambda x: x.update(status="error"))

        t = threading.Thread(target=work, daemon=True)
        self.threads[run_id] = t
        t.start()
        return {"running": True}

    def _editable(self, run_id) -> dict:
        run = inbox.load_run(self.settings, run_id)
        if self._busy(run_id) and run.get("status") == "running":
            raise ToolError("invalid_request", "에이전트가 작업 중입니다. 멈춘 뒤 고치거나, 입력창으로 고칠 점을 보내 주세요")
        return run

    def _tell_agent(self, run_id, text):
        inbox.update_run(self.settings, run_id, lambda r: r.setdefault("messages", []).append(
            {"text": text[:4000], "at": view.now(), "delivered": False, "from": "edit"}))

    @Slot(str, result=str)
    @safe
    def specForEdit(self, run_id):
        """요구사항·합격 케이스를 편집 화면에 채울 값 — 원장의 요구사항과 마지막 캡처의 케이스(자동 생성분 제외)."""
        run = inbox.load_run(self.settings, run_id)
        conn, _ = project.load(self.settings, run["project_id"])
        from oss_analyzer.core import make
        reqs = [{"id": r["id"], "text": r["text"]} for r in make.requirements(conn, run["port_id"])]
        cap = port._last_check(conn, run["port_id"], "capture")
        cases = []
        if cap and Path(cap["detail"].get("golden", "")).is_file():
            g = json.loads(Path(cap["detail"]["golden"]).read_text(encoding="utf-8"))
            for c in g.get("cases") or []:
                if c.get("generated"):
                    continue
                cases.append({k: v for k, v in c.items() if k not in ("new", "generated", "from")})
        return {"requirements": reqs, "cases": cases}

    @Slot(str, str, result=str)
    @safe
    def saveSpec(self, run_id, payload):
        """사용자가 고친 요구사항·합격 케이스를 저장한다 (원장에 사용자 작성으로 기록). 승인은 따로 — 바뀐 내용으로 다시 승인."""
        run = self._editable(run_id)
        v = json.loads(payload)
        conn, proj = project.load(self.settings, run["project_id"])
        from oss_analyzer.core import make
        who = f"user:{getpass.getuser()}"
        notes = []
        if v.get("requirements") is not None:
            before = {r["id"]: r["text"] for r in make.requirements(conn, run["port_id"])}
            make.set_requirements(conn, run["port_id"], v["requirements"], actor=who)
            after = {r["id"]: r["text"] for r in make.requirements(conn, run["port_id"])}
            changed = [k for k in after if before.get(k) != after[k]] + [f"{k}(삭제)" for k in before if k not in after]
            if changed:
                notes.append("요구사항 " + ", ".join(changed))
        if v.get("cases") is not None:
            if not v["cases"]:
                raise ToolError("invalid_request", "합격 케이스가 비어 있습니다")
            path = Path(run["target_root"]) / ".oss" / "cases.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"cases": v["cases"]}, ensure_ascii=False, indent=2), encoding="utf-8")
            port.capture(conn, proj, self.settings, run["port_id"], str(path))
            notes.append(f"합격 케이스 {len(v['cases'])}개")
        if notes:
            inbox.log(self.settings, run_id, "user_edit", what=" · ".join(notes))
            self._tell_agent(run_id, "사용자가 앱에서 직접 고쳤다 — " + " · ".join(notes)
                             + ". 원장과 .oss/cases.json 의 지금 내용을 기준으로 진행한다 (다시 바꾸지 않는다).")
        return {"saved": notes, "review_sig": view.detail(self.settings, run_id).get("review_sig")}

    @Slot(str, str, result=str)
    @safe
    def savePlan(self, run_id, payload):
        """사용자가 고친 계획을 저장한다. 승인은 따로 — 저장하면 승인 전 상태가 된다."""
        self._editable(run_id)
        v = json.loads(payload)
        steps = [{"title": str(s.get("title", "")).strip()[:200], "files": [str(f).strip() for f in s.get("files") or [] if str(f).strip()][:20],
                  "commands": [str(c).strip() for c in s.get("commands") or [] if str(c).strip()][:10],
                  "requirements": s.get("requirements") or [], "status": s.get("status") or "todo"}
                 for s in v.get("steps") or [] if str(s.get("title", "")).strip()]
        if not steps:
            raise ToolError("invalid_request", "단계가 비어 있습니다")

        def put(r):
            old = r.get("plan") or {}
            hist = (old.get("history") or []) + ([{"steps": old["steps"], "summary": old.get("summary")}] if old.get("steps") else [])
            r["plan"] = {"steps": steps, "summary": str(v.get("summary") or old.get("summary") or "").strip()[:1000],
                         "approved": False, "history": hist[-5:], "at": view.now(), "edited_by": getpass.getuser(),
                         **({"design": str(v["design"] if v.get("design") is not None else old.get("design") or "")[:6000]}
                            if (v.get("design") is not None or old.get("design")) else {})}

        inbox.update_run(self.settings, run_id, put)
        inbox.log(self.settings, run_id, "user_edit", what=f"계획 {len(steps)}단계")
        self._tell_agent(run_id, f"사용자가 앱에서 계획을 직접 고쳤다 ({len(steps)}단계). 승인되면 이 계획대로 진행한다.")
        return {"saved": True, "plan_sig": view.detail(self.settings, run_id).get("plan_sig")}

    # -- 실행 목록 정리
    @Slot(str, str, result=str)
    @safe
    def renameRun(self, run_id, name):
        name = name.strip()[:60]
        if not name:
            raise ToolError("invalid_request", "이름이 비어 있습니다")
        inbox.update_run(self.settings, run_id, lambda r: r.update(name=name))
        return {"name": name}

    @Slot(str, bool, result=str)
    @safe
    def archiveRun(self, run_id, archived):
        if archived and self._busy(run_id):
            raise ToolError("invalid_request", "진행 중인 실행은 숨길 수 없습니다. 먼저 멈춰 주세요")
        inbox.update_run(self.settings, run_id, lambda r: r.update(archived=bool(archived)))
        return {"archived": bool(archived)}

    # -- 바로가기 (시작 메뉴·바탕화면)
    def _prefs(self) -> dict:
        from oss_analyzer.core import storage
        return storage.load_prefs(self.settings)

    @Slot(result=str)
    @safe
    def shortcutStatus(self):
        from oss_analyzer.core import shortcut
        return {**shortcut.status(), "dismissed": bool(self._prefs().get("shortcut_dismissed"))}

    @Slot(str, result=str)
    @safe
    def installShortcut(self, where):
        from oss_analyzer.app.main import write_icon_files
        from oss_analyzer.core import shortcut
        locs = tuple(w for w in where.split(",") if w in shortcut.LOCATIONS) or shortcut.LOCATIONS
        return shortcut.create(self.settings, locs, icon=write_icon_files(self.settings.home))

    @Slot(result=str)
    @safe
    def dismissShortcut(self):
        from oss_analyzer.core import storage
        storage.save_prefs(self.settings, {"shortcut_dismissed": True})
        return {"dismissed": True}

    # -- 데이터 (저장 위치·크기·휴지통·정리)
    dataReady = Signal(str)

    @Slot(result=str)
    @safe
    def dataReport(self):
        """크기 계산은 몇 초 걸릴 수 있어 뒤에서 하고 dataReady 로 보낸다. 바로는 크기 없이 돌려준다."""
        from oss_analyzer.core import storage

        def work():
            try:
                self.dataReady.emit(json.dumps({"ok": True, "data": storage.report(self.settings)}, ensure_ascii=False))
            except Exception as e:  # noqa: BLE001
                self.dataReady.emit(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))

        threading.Thread(target=work, daemon=True).start()
        return storage.report(self.settings, sizes=False)

    @Slot(str, result=str)
    @safe
    def trashRun(self, run_id):
        from oss_analyzer.core import storage
        if self._busy(run_id):
            raise ToolError("invalid_request", "진행 중인 실행은 지울 수 없습니다. 먼저 멈춰 주세요")
        return storage.trash_run(self.settings, run_id)

    @Slot(str, result=str)
    @safe
    def restoreRun(self, name):
        from oss_analyzer.core import storage
        return storage.restore_run(self.settings, name)

    @Slot(result=str)
    @safe
    def emptyTrash(self):
        from oss_analyzer.core import storage
        return storage.empty_trash(self.settings)

    @Slot(str, result=str)
    @safe
    def setDataPrefs(self, payload):
        from oss_analyzer.core import storage
        return storage.save_prefs(self.settings, json.loads(payload))

    @Slot(str, str, result=str)
    @safe
    def setRunOptions(self, run_id, payload):
        """사용자가 실행 중에 바꾸는 설정 — 모델(다음 라운드부터), 권한 규칙(바로), 최대 라운드.
        에이전트는 규칙을 엄격하게만 바꿀 수 있지만 사람은 어느 쪽으로든 바꿀 수 있다."""
        v = json.loads(payload)

        def put(r):
            if "model" in v:
                r["model"] = (v["model"] or "").strip() or None
            if "effort" in v:
                r["effort"] = (v["effort"] or "").strip() or None
            if isinstance(v.get("policy"), dict):
                r["policy"] = {k: (v["policy"].get(k) if v["policy"].get(k) in inbox.DECISIONS else r["policy"].get(k, "ask"))
                               for k in inbox.KINDS}
            if "max_rounds" in v:
                n = int(v["max_rounds"] or 0)
                r["max_rounds"] = 0 if n <= 0 else max(int(r.get("round") or 0) + 1, min(500, n))
            if "plan_approval" in v:
                r["plan_approval"] = bool(v["plan_approval"])
            if v.get("confirm_at") in ("start", "end"):
                r["confirm_at"] = v["confirm_at"]

        run = inbox.update_run(self.settings, run_id, put)
        inbox.log(self.settings, run_id, "options", model=run.get("model"), mode=inbox.mode_of(run["policy"]),
                  max_rounds=run.get("max_rounds"))
        return {"model": run.get("model"), "effort": run.get("effort"), "policy": run["policy"], "max_rounds": run.get("max_rounds"),
                "plan_approval": run.get("plan_approval", True), "confirm_at": run.get("confirm_at", "start")}

    @Slot(str, result=str)
    @safe
    def stop(self, run_id):
        if run_id in self.runners:
            self.runners[run_id].stop()
        if runner.lock_owner(self.settings, run_id) is not None:
            runner.request_stop(self.settings, run_id)  # CLI·다른 창에서 도는 실행도 멈춘다
        return {"stopping": True}

    # -- 사람의 결정 (앱 프로세스 안에서만 기록된다)
    @Slot(str, str, result=str)
    @safe
    def approveReviews(self, run_id, shown_sig):
        run = inbox.load_run(self.settings, run_id)
        conn, _ = project.load(self.settings, run["project_id"])
        reviews = port.pending_reviews(conn, run["port_id"])
        if view.sig(reviews) != shown_sig:  # 화면을 본 뒤 에이전트가 내용을 바꿨다 — 본 것만 승인한다
            raise ToolError("conflict", "확인하는 사이에 내용이 바뀌었습니다. 새 내용을 보고 다시 승인해 주세요")
        guard.approve(conn, run["port_id"], reviews, channel="desktop-app", approved_by=getpass.getuser())
        inbox.log(self.settings, run_id, "approved", items=[r["item_id"] for r in reviews])
        return {"approved": len(reviews)}

    @Slot(str, str, result=str)
    @safe
    def approvePlan(self, run_id, shown_sig):
        def ok(r):
            plan = r.get("plan") or {}
            if not plan.get("steps"):
                raise ToolError("invalid_request", "승인할 계획이 없습니다")
            if view.sig(view.plan_core(plan)) != shown_sig:
                raise ToolError("conflict", "확인하는 사이에 계획이 바뀌었습니다. 새 계획을 보고 다시 승인해 주세요")
            plan["approved"] = True
            plan["approved_by"] = getpass.getuser()
            plan["approved_at"] = view.now()

        inbox.update_run(self.settings, run_id, ok)
        inbox.log(self.settings, run_id, "plan_approved")
        return {"approved": True}

    @Slot(str, str, str, result=str)
    @safe
    def answer(self, run_id, request_id, payload):
        inbox.answer(self.settings, run_id, request_id, json.loads(payload))
        return {"answered": True}

    @Slot(str, str, result=str)
    @safe
    def sendMessage(self, run_id, text):
        text = text.strip()
        if not text:
            raise ToolError("invalid_request", "내용이 비어 있습니다")
        def add(run):
            run.setdefault("messages", []).append({"text": text[:4000], "at": view.now(), "delivered": False})
            if run.get("status") == "done":
                run["status"] = "stopped"
                run.pop("handoff", None)

        inbox.update_run(self.settings, run_id, add)
        inbox.log(self.settings, run_id, "user_message", text=text[:4000])
        return {"queued": True}

    @Slot(str, result=str)
    @safe
    def accept(self, run_id):
        inbox.update_run(self.settings, run_id, lambda r: r.update(accepted=True, accepted_at=view.now()))
        inbox.log(self.settings, run_id, "accepted")
        return {"accepted": True}

    @Slot(str, str, result=str)
    @safe
    def restore(self, run_id, checkpoint_id):
        if self._busy(run_id):
            raise ToolError("invalid_request", "진행 중에는 되돌릴 수 없습니다. 먼저 멈춰 주세요")
        run = inbox.load_run(self.settings, run_id)
        cp = Checkpoints(inbox.run_dir(self.settings, run_id), run["target_root"])
        label = next((c["label"] for c in cp.list() if c["id"] == checkpoint_id), "")
        out = cp.restore(checkpoint_id)
        inbox.log(self.settings, run_id, "restored", checkpoint=checkpoint_id, label=label)
        return out

    @Slot(str, str, result=str)
    @safe
    def revertFile(self, run_id, path):
        if self._busy(run_id):
            raise ToolError("invalid_request", "진행 중에는 되돌릴 수 없습니다")
        run = inbox.load_run(self.settings, run_id)
        cp = Checkpoints(inbox.run_dir(self.settings, run_id), run["target_root"])
        base = cp.list()[-1]["id"] if cp.list() else None
        if not base:
            raise ToolError("not_found", "체크포인트가 없습니다")
        from oss_analyzer.core.checkpoints import _git
        root = Path(run["target_root"])
        if not inbox._inside(path, root) or (root / path).resolve() == root.resolve():
            raise ToolError("invalid_request", "대상 폴더 밖 경로")
        target = (root / path).resolve()
        exists = _git(cp.shadow, root, "cat-file", "-e", f"{base}:{path}", check=False).returncode == 0
        if exists:
            _git(cp.shadow, root, "checkout", "-q", base, "--", path)
        elif target.is_file():
            target.unlink()
        inbox.log(self.settings, run_id, "restored", label=f"{path} 되돌림")
        return {"reverted": path}

    # -- 운영체제
    @Slot(result=str)
    @safe
    def pickFolder(self):
        return QFileDialog.getExistingDirectory(self.window, "폴더 선택") or ""

    @Slot(str, result=str)
    @safe
    def openFolder(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        return True

    @Slot(str, result=str)
    @safe
    def loginAgent(self, agent_id):
        st = agents.get(agent_id).status()
        cmd = st.get("login")
        if not cmd:
            raise ToolError("not_found", st.get("hint") or "로그인 명령이 없습니다")
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        subprocess.Popen(cmd, creationflags=flags)
        return {"opened": True}
