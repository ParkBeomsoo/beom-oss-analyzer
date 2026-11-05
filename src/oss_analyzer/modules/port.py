"""포팅 도구 — 분석한 기능을 내 저장소로 옮기거나 다른 언어로 다시 만들고, 원본과 같은 동작인지 검증."""
from __future__ import annotations

from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

from oss_analyzer.common.response import Envelope, ToolError, enveloped
from oss_analyzer.core import port, port_guard, project
from oss_analyzer.server import mcp, settings


class _Approval(BaseModel):
    approve: bool = Field(description="위 항목을 모두 승인")


@mcp.tool()
@enveloped("port.slice")
def oss_port_slice(project_id: str, targets: list[str], within: list[str] | None = None, depth: int = 10) -> dict:
    """옮길 범위: 진입점("POST /api/v1/orders") 또는 심볼에서 앞으로 닫힘 — 호출·인터페이스 디스패치·등록(+import 배선)·
    모듈 상태·설정 키·외부 import/호출(대상 언어에서 대체할 것)·명세 역할 테스트·파일별 라이선스·열린 미확인."""
    conn, proj = project.load(settings, project_id)
    return port.slice_(conn, proj, targets, depth=depth, within=within)


@mcp.tool()
@enveloped("port.create")
def oss_port_create(project_id: str, name: str, targets: list[str], target_language: str, target_root: str,
                    author: str = "agent", within: list[str] | None = None, depth: int = 10) -> dict:
    """포팅 원장 생성 — 슬라이스 항목(심볼·상태·배선)마다 pending. target_root 는 내 저장소 경로."""
    conn, proj = project.load(settings, project_id)
    return port.create(conn, proj, name=name, targets=targets, target_language=target_language,
                       target_root=target_root, author=author, within=within, depth=depth)


@mcp.tool()
@enveloped("port.map")
def oss_port_map(project_id: str, port_id: str, updates: list[dict] | None = None, follow: bool = False,
                 actor: str = "agent") -> dict:
    """항목 매핑. updates: [{item, status: ported|omitted|replaced, target: "내 저장소 경로:줄"(ported), note(omitted·replaced)}].
    ported 의 target 은 실제로 있는지와 줄 내용을 기록하고, 이후 줄이 밀리거나 바뀌면 게이트에서 잡는다.
    follow=True: 이동한 대상(같은 내용이 한 곳에만 있음)을 새 줄로 갱신."""
    conn, _ = project.load(settings, project_id)
    if follow:
        return port.follow(conn, port_id, actor)
    return port.map_items(conn, port_id, updates or [], actor)


@mcp.tool()
@enveloped("port.accept")
def oss_port_accept(project_id: str, port_id: str, cases: list[str], reason: str, actor: str = "agent") -> dict:
    """의도적인 동작 차이 허용 '제안' (케이스 이름 패턴, * 가능 — 예: "*~json.items.0.price=소수문자열"). 사유 필수.
    제안만 기록된다 — 사용자가 oss_port_approve 확인 창(또는 터미널 port-approve)에서 승인해야 게이트가 통과한다."""
    conn, _ = project.load(settings, project_id)
    return port.accept(conn, port_id, cases, reason, actor)


@mcp.tool()
@enveloped("port.status")
def oss_port_status(project_id: str, port_id: str | None = None) -> dict:
    """포팅 진행·게이트·next_step(다음 할 일). 게이트: 매핑 · 대상 위치 · 차등 검증(지금 코드 기준) · 케이스 충분성 ·
    사람 승인 · 기록 무결 · 라이선스. 다음에 할 일은 next_step 을 따른다."""
    conn, _ = project.load(settings, project_id)
    return port.status_(conn, port_id) if port_id else port.list_(conn)


@mcp.tool()
@enveloped("port.capture")
def oss_port_capture(project_id: str, port_id: str, cases_file: str, source_url: str | None = None,
                     source_cmd: str | None = None, source_cli: str | None = None, trusted: bool = False,
                     source_image: str | None = None, explore_n: int = 0, coverage_cmd: str | None = None,
                     coverage_report: str | None = None) -> dict:
    """원본에 케이스(HTTP·CLI·함수 호출)를 돌려 골든 응답을 저장.
    원본 실행: source_image(Docker 격리) 또는 trusted=True(로컬 임시 사본). explore_n: 입력 변형 케이스 추가 생성.
    coverage_report(+coverage_cmd): 원본 커버리지로 케이스가 슬라이스 함수를 모두 거쳤는지 확인(게이트)."""
    conn, proj = project.load(settings, project_id)
    return port.capture(conn, proj, settings, port_id, cases_file, source_url=source_url, source_cmd=source_cmd,
                        source_cli=source_cli, trusted=trusted, source_image=source_image, explore_n=explore_n,
                        coverage_cmd=coverage_cmd, coverage_report=coverage_report)


@mcp.tool()
@enveloped("port.verify")
def oss_port_verify(project_id: str, port_id: str, target_url: str | None = None, target_cmd: str | None = None,
                    target_cli: str | None = None, cases_file: str | None = None, source_url: str | None = None,
                    source_cmd: str | None = None, source_cli: str | None = None, trusted: bool = False,
                    source_image: str | None = None, concurrency: int = 0, target_coverage_cmd: str | None = None,
                    target_coverage_report: str | None = None) -> dict:
    """대상(내 코드, 어떤 언어든)을 원본과 차등 비교. 기본은 골든 대비, source_* 를 주면 실시간.
    concurrency>1: 케이스마다 대상에 동시 요청. 전부 통과하면 원본 처리 함수에 L3 근거(differential)."""
    conn, proj = project.load(settings, project_id)
    return port.verify(conn, proj, settings, port_id, target_url=target_url, target_cmd=target_cmd, target_cli=target_cli,
                       cases_file=cases_file, source_url=source_url, source_cmd=source_cmd, source_cli=source_cli,
                       trusted=trusted, source_image=source_image, concurrency=concurrency,
                       target_coverage_cmd=target_coverage_cmd, target_coverage_report=target_coverage_report)


@mcp.tool()
@enveloped("port.finish")
def oss_port_finish(project_id: str, port_id: str, actor: str = "agent") -> dict:
    """완료 판정 — 게이트가 모두 충족될 때만 발급하고, 아니면 미충족 게이트와 next_step 을 돌려준다.
    포팅이 '완료'라고 보고할 때는 이 결과(finished id)로만 말한다."""
    conn, _ = project.load(settings, project_id)
    return port.finish(conn, port_id, actor)


@mcp.tool()
async def oss_port_approve(project_id: str, port_id: str, ctx: Context) -> dict:
    """사람 확인 항목(차이 허용·생략·케이스 변경)을 사용자에게 확인 창으로 보여 주고 승인받는다.
    응답은 사용자가 직접 한다 — 에이전트가 대신 답할 수 없다. 클라이언트가 확인 창을 지원하지 않으면
    사용자에게 터미널에서 `oss-analyzer port-approve <project> <port>` 를 직접 실행해 달라고 요청한다."""
    env = Envelope("port.approve")
    try:
        conn, _ = project.load(settings, project_id)
        items = port.pending_reviews(conn, port_id)
        if not items:
            return env.ok({"approved": [], "note": "승인 대기 없음"})
        message = f"포팅 {port_id} — 승인 대기 {len(items)}건\n\n{port_guard.render(items)}\n\n모두 승인하시겠습니까?"
        try:
            res = await ctx.elicit(message=message, schema=_Approval)
        except Exception as e:  # noqa: BLE001 — 확인 창 미지원 클라이언트
            raise ToolError("human_required", f"확인 창을 띄우지 못함 ({type(e).__name__})",
                            recovery=f"사용자에게 터미널에서 'oss-analyzer port-approve {project_id} {port_id}' 를 직접 실행해 달라고 요청") from None
        if res.action != "accept" or not getattr(res.data, "approve", False):
            return env.ok({"approved": [], "note": f"사용자가 승인하지 않음 ({res.action})"})
        # 확인 창을 띄운 사이에 내용이 바뀌었으면 승인하지 않는다
        now = {(i["item_id"], i["fingerprint"]) for i in port.pending_reviews(conn, port_id)}
        chosen = [i for i in items if (i["item_id"], i["fingerprint"]) in now]
        done = port_guard.approve(conn, port_id, chosen, channel="mcp-elicitation", approved_by="user(elicitation)")
        st = port.status_(conn, port_id, brief=True)
        return env.ok({"approved": done, **{k: st[k] for k in ("gates", "next_step", "approvals")}})
    except ToolError as e:
        return env.fail(e.code, e.message, e.recovery, e.data)
    except Exception as e:  # noqa: BLE001
        return env.fail("internal", f"{type(e).__name__}: {e}")
