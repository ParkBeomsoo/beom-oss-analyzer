"use strict";
// 화면 상태와 그리기. 데이터는 Qt WebChannel 로 같은 프로세스의 툴킷(Api)에서만 받는다.
let api = null;
const S = { runs: [], agents: [], kinds: {}, policy: {}, current: null, detail: null, tab: "progress", mode: "request",
  draft: { request: "", target_root: "", context_dir: "", source: "", feature: "", agent: "claude", model: "", effort: "", max_rounds: 0, confirm_at: "start",
           auth: "subscription", plan_approval: true, policy: null }, permModes: [], preparing: false,
  changes: null, file: null, sig: { feed: "", right: "", side: "" }, startedAt: null };

const $ = (s) => document.querySelector(s);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
function call(name, ...args) {
  return new Promise((resolve) => api[name](...args, (raw) => {
    const r = JSON.parse(raw);
    if (!r.ok) toast(r.error);
    resolve(r);
  }));
}
let toastTimer;
function toast(text) {
  const t = $("#toast"); t.textContent = text; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}
const fmtMoney = (v) => v == null ? "—" : "$" + Number(v).toFixed(2);
function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "방금"; if (s < 3600) return Math.floor(s / 60) + "분 전";
  if (s < 86400) return Math.floor(s / 3600) + "시간 전"; return Math.floor(s / 86400) + "일 전";
}

// ---------------------------------------------------------------- 공용 조각: 아이콘 · 팝오버 메뉴 · 알약 버튼 · 토글
const ICONS = {
  folder: '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2h8.5A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/>',
  down: '<path d="m7 10 5 5 5-5"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
  shield: '<path d="M12 3.5 5 6v5.5c0 4.2 2.9 7.6 7 9 4.1-1.4 7-4.8 7-9V6z"/>',
  chip: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
  agent: '<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 3v4M9 12h.01M15 12h.01"/>',
  play: '<path d="M8 5.5v13l11-6.5z"/>',
  clip: '<path d="m20 11-8.5 8.5a5 5 0 0 1-7-7L13 4a3.3 3.3 0 0 1 4.7 4.7L9.2 17.2a1.7 1.7 0 0 1-2.4-2.4L14.5 7"/>',
  gauge: '<path d="M4.5 16a7.5 7.5 0 1 1 15 0"/><path d="m12 16 3.5-4"/>',
  book: '<path d="M5 4.5h9.5A2.5 2.5 0 0 1 17 7v12.5H7.5A2.5 2.5 0 0 1 5 17z"/><path d="M5 17a2.5 2.5 0 0 1 2.5-2.5H17"/>',
};
function ico(name, cls) {
  const s = h("span", { class: "ico" + (cls ? " " + cls : "") });
  s.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
  return s;
}
let openPop = null;
function closePop() { if (openPop) { openPop.remove(); openPop = null; } }
document.addEventListener("mousedown", (e) => { if (openPop && !openPop.contains(e.target) && !e.target.closest(".pill.open")) closePop(); });
// 앵커 위(공간이 없으면 아래)에 뜨는 메뉴. items: [{id, label, desc, note, on}] 또는 content(요소)
function popover(anchor, { title, items, onPick, content, width }) {
  closePop();
  const pop = h("div", { class: "pop", style: width ? `width:${width}px` : null },
    title ? h("div", { class: "pt", text: title }) : null,
    ...(items || []).map((it) => h("button", { class: "pi" + (it.on ? " on" : ""), onclick: () => { closePop(); onPick(it); } },
      h("span", { class: "pl" }, h("b", { text: it.label }), it.note ? h("i", { text: it.note }) : null),
      it.desc ? h("span", { class: "pd", text: it.desc }) : null,
      it.on ? ico("check", "ck") : null)),
    content || null);
  document.body.append(pop);
  const r = anchor.getBoundingClientRect(), pr = pop.getBoundingClientRect();
  const left = Math.max(8, Math.min(r.left, innerWidth - pr.width - 8));
  let top = r.top - pr.height - 8 > 8 ? r.top - pr.height - 8 : r.bottom + 8;
  if (top + pr.height > innerHeight - 8) top = Math.max(8, innerHeight - pr.height - 8);  // 화면 밖으로 나가지 않게
  pop.style.left = left + "px"; pop.style.top = top + "px";
  openPop = pop;
  return pop;
}
function pill(icon, label, value, onclick, cls) {
  const b = h("button", { class: "pill" + (cls ? " " + cls : ""), title: label }, ico(icon), value != null ? h("span", { class: "pv", text: value }) : h("span", { class: "pv", text: label }), ico("down", "dn"));
  b.addEventListener("click", (e) => { e.stopPropagation(); if (openPop && b.classList.contains("open")) { closePop(); return; }
    document.querySelectorAll(".pill.open").forEach((x) => x.classList.remove("open")); b.classList.add("open"); onclick(b);
    const pop = openPop; if (pop) new MutationObserver((_, o) => { if (!pop.isConnected) { b.classList.remove("open"); o.disconnect(); } }).observe(document.body, { childList: true }); });
  return b;
}
function segmented(options, value, onchange) {
  return h("div", { class: "seg" }, ...options.map(([v, t]) => h("button", { class: String(v) === String(value) ? "on" : "", onclick: (e) => { e.stopPropagation(); onchange(v); } }, t)));
}
function toggle(on, onchange) {
  return h("button", { class: "tg" + (on ? " on" : ""), role: "switch", "aria-checked": on ? "true" : "false", onclick: (e) => { e.stopPropagation(); onchange(!on); } }, h("i"));
}
function modeOf(policy) {
  const m = S.permModes.find((x) => Object.entries(x.policy).every(([k, v]) => (policy || {})[k] === v));
  return m ? m.id : "custom";
}
const modeLabel = (policy) => { const id = modeOf(policy); return id === "custom" ? "직접 설정" : S.permModes.find((m) => m.id === id).label; };
// 고르지 않았을 때 실제로 쓰이는 값 — 에이전트 설정 파일, 없으면 최근에 실제로 쓴 모델 (agents.current)
function currentOf(agentId) { const a = S.agents.find((x) => x.id === agentId); return (a && a.current) || {}; }
function modelName(agentId, id) {
  const a = S.agents.find((x) => x.id === agentId), m = a && (a.models || []).find((x) => x.id === id);
  if (m && m.id) return m.label;
  return id ? id.replace(/^claude-/, "").replace(/-(\d+)-(\d+)$/, " $1.$2") : "";
}
function modelLabel(agentId, model) {
  if (model) return modelName(agentId, model);
  const cur = currentOf(agentId);
  return cur.model ? modelName(agentId, cur.model) : "에이전트 설정";
}
// 에이전트 · 모델 · 권한 메뉴 — 홈 초안과 실행 중 설정이 같은 메뉴를 쓴다 (get: 현재 값, set: 바꾸기)
function agentMenu(anchor, cur, set) {
  popover(anchor, { title: "에이전트", width: 300, items: S.agents.filter((a) => a.exe || a.id === "claude").map((a) =>
    ({ id: a.id, label: a.name.replace(" (실험적)", ""), note: a.ready ? "" : (a.exe ? "로그인 필요" : "설치 필요"), desc: a.note || "", on: a.id === cur })),
    onPick: (it) => set(it.id) });
}
function modelMenu(anchor, agentId, cur, set) {
  const a = S.agents.find((x) => x.id === agentId) || { models: [] };
  const custom = h("input", { class: "pin", placeholder: "다른 모델 이름 직접 입력 후 Enter", value: (a.models || []).some((m) => m.id === (cur || "")) ? "" : (cur || ""),
    onkeydown: (e) => { if (e.key === "Enter") { closePop(); set(e.target.value.trim()); } } });
  popover(anchor, { title: `모델 — ${a.name ? a.name.replace(" (실험적)", "") : ""}`, width: 290,
    items: (a.models || [{ id: "", label: "기본값" }]).map((m) => ({ id: m.id, label: m.id ? m.label : "기본 모델", note: m.note || (m.id ? "" : "에이전트 설정"), on: (cur || "") === m.id })),
    onPick: (it) => set(it.id), content: h("div", { class: "pf" }, custom) });
}
function effortLevels(agentId, model) {
  const a = S.agents.find((x) => x.id === agentId); if (!a || !a.efforts) return [];
  return a.efforts[model || ""] || a.efforts[""] || [];
}
function effortLabel(agentId, model, effort) {
  if (!effort) { const cur = currentOf(agentId), lv = cur.effort && effortLevels(agentId, model || cur.model).find((x) => x.id === cur.effort);
    return lv ? `노력 ${lv.label}` : "노력 자동"; }
  const l = effortLevels(agentId, model).find((x) => x.id === effort); return l ? `노력 ${l.label}` : `노력 ${effort}`;
}
function effortMenu(anchor, agentId, model, cur, set) {
  popover(anchor, { title: "노력 — 얼마나 깊이 생각할지", width: 300, items: [
      { id: "", label: "기본", note: "모델 기본값", on: !cur },
      ...effortLevels(agentId, model).map((l) => ({ id: l.id, label: l.label, note: l.id, desc: l.desc, on: cur === l.id }))],
    onPick: (it) => set(it.id) });
}
// 에이전트 · 모델 · 노력을 한 메뉴에서 (Claude 데스크톱의 모델 고르기처럼)
function brainLabel(agentId, model, effort) {
  const cur = currentOf(agentId), eff = effort || cur.effort;
  const lv = effortLevels(agentId, model || cur.model).find((x) => x.id === eff);
  return [agentName(agentId), modelLabel(agentId, model), lv ? lv.label : null].filter(Boolean).join(" · ");
}
function brainPop(anchor, cur, set, allowAgent) {
  const draw = () => {
    const c = cur();
    const a = S.agents.find((x) => x.id === c.agent) || { models: [] };
    const levels = effortLevels(c.agent, c.model);
    const custom = h("input", { class: "pin", placeholder: "다른 모델 이름 직접 입력 후 Enter",
      value: (a.models || []).some((m) => m.id === (c.model || "")) ? "" : (c.model || ""),
      onkeydown: (e) => { if (e.key === "Enter") { set({ model: e.target.value.trim() }); draw(); } } });
    const pop = popover(anchor, { width: 340, content: h("div", { class: "brain" },
      allowAgent ? h("div", { class: "pt", text: "에이전트" }) : null,
      allowAgent ? h("div", { class: "agrid" }, ...S.agents.filter((x) => x.exe || x.id === "claude").map((x) =>
        h("button", { class: "ag" + (x.id === c.agent ? " on" : "") + (x.ready ? "" : " off"), title: x.hint || x.note || "",
          onclick: (e) => { e.stopPropagation(); S.agentPicked = true; set({ agent: x.id, model: "", effort: "" }); draw(); } },
          h("b", { text: x.name.replace(" (실험적)", "") }), h("i", { text: x.ready ? "연결됨" : x.ready === null && x.exe ? "확인 중" : x.exe ? "로그인 필요" : "설치 필요" })))) : null,
      h("div", { class: "pt", text: "모델" }),
      ...(() => {
        const cur = currentOf(c.agent), inUse = c.model || cur.model || "";
        const list = (a.models || []).filter((m) => m.id);
        if (inUse && !list.some((m) => m.id === inUse)) list.unshift({ id: inUse, label: modelName(c.agent, inUse) });
        return list.map((m) => h("button", { class: "pi" + (inUse === m.id ? " on" : ""),
          onclick: (e) => { e.stopPropagation(); const keep = effortLevels(c.agent, m.id).some((l) => l.id === c.effort); set({ model: m.id, ...(keep ? {} : { effort: "" }) }); draw(); } },
          h("span", { class: "pl" }, h("b", { text: m.label }),
            h("i", { text: !c.model && m.id === cur.model ? `현재 · ${cur.source || "설정"}` : (m.note || "") })),
          inUse === m.id ? ico("check", "ck") : null));
      })(),
      h("div", { class: "pf" }, custom),
      levels.length ? h("div", { class: "pt sub2", text: "노력 — 얼마나 깊이 생각할지" }) : null,
      levels.length ? h("div", { class: "effrow" }, segmented(levels.map((l) => [l.id, l.label]), c.effort || currentOf(c.agent).effort || "",
        (v) => { set({ effort: v }); draw(); })) : null,
      levels.length && !c.effort ? h("div", { class: "pnote", text: currentOf(c.agent).effort
        ? "지금은 모델 기본값을 씁니다 — 고르면 그 단계로 고정합니다" : "지금은 에이전트가 정합니다 — 고르면 그 단계로 고정합니다" }) : null,
      levels.length && c.effort ? h("div", { class: "pnote", text: (levels.find((l) => l.id === c.effort) || {}).desc || "" }) : null) });
    return pop;
  };
  draw();
}
function permMenu(anchor, policy, set, openDetail) {
  const cur = modeOf(policy);
  popover(anchor, { title: "권한", width: 340, items: [
      ...S.permModes.map((m) => ({ id: m.id, label: m.label, desc: m.desc, on: cur === m.id })),
      { id: "custom", label: "직접 설정…", desc: "작업 종류마다 허용·물어보기·거절을 정합니다", on: cur === "custom" }],
    onPick: (it) => it.id === "custom" ? openDetail() : set({ ...S.permModes.find((m) => m.id === it.id).policy }) });
}
function policyRows(policy, set) {
  return h("div", { class: "prow-list" }, ...Object.entries(S.kinds).map(([k, t]) => h("div", { class: "prow" },
    h("span", { text: t }), segmented([["allow", "허용"], ["ask", "확인"], ["refuse", "거절"]], policy[k], (v) => set({ ...policy, [k]: v })))));
}

// 저장 공간 — 어디에 무엇이 얼마나, 휴지통, 자동 정리
const AREA_KO = { projects: ["원장·원본 클론", "분석·포팅 기록과 원본 저장소 — 핵심 기록"], runs: ["실행 기록", "대화 흐름·질문·체크포인트"],
  trash: ["휴지통", "지운 실행 — 정한 날이 지나면 비움"], cache: ["캐시", "다시 만들 수 있음 — 지워도 됨"], "cbm-cache": ["구조 지도 캐시", "다시 만들 수 있음 — 지워도 됨"],
  work: ["임시 사본", "검증 중에만 쓰고 지움"], engines: ["분석 엔진", "setup 으로 다시 설치 가능"], bin: ["엔진 실행 파일", "setup 으로 다시 설치 가능"],
  node: ["언어 서버", "setup 으로 다시 설치 가능"], backups: ["백업", "스킬을 덮어쓰기 전 보관본"], app: ["앱 아이콘", "바로가기가 가리킴"] };
function fmtBytes(n, atLeast) {
  if (n == null) return "계산 중…";
  const u = ["B", "KB", "MB", "GB"]; let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (atLeast ? "≥ " : "") + (i ? n.toFixed(n < 10 ? 1 : 0) : n) + " " + u[i];
}
async function dataPop(anchor) {
  const r = await call("dataReport"); if (!r.ok) return;
  S.data = r.data; drawData(anchor);
}
function drawData(anchor) {
  const D = S.data; if (!D) return;
  const prefs = D.settings || {};
  const pop = popover(anchor, { width: 380, content: h("div", { class: "optpop datapop" },
    h("div", { class: "pt", text: "저장 공간 — 모두 이 컴퓨터에만 있습니다" }),
    h("div", { class: "droot" }, h("span", { class: "mono", text: D.root }), h("button", { class: "btn quiet sm", onclick: () => call("openFolder", D.root) }, "폴더 열기")),
    ...D.areas.map((a) => h("div", { class: "darea" }, h("div", {}, h("b", { text: (AREA_KO[a.name] || [a.name])[0] }), h("span", { text: (AREA_KO[a.name] || ["", ""])[1] })),
      h("span", { class: "dsize", text: fmtBytes(a.bytes, a.at_least) }))),
    h("div", { class: "pt sub2", text: `실행 ${D.runs.total}개 · 끝남 ${D.runs.finished} · 숨김 ${D.runs.archived}` }),
    h("div", { class: "orow" }, h("div", {}, h("b", { text: "자동 정리" }), h("span", { text: "끝났거나 숨긴 실행을 이 기간 뒤 휴지통으로 (원장은 남음)" })),
      segmented([["", "끔"], [30, "30일"], [90, "90일"]], prefs.cleanup_days || "", async (v) => {
        const x = await call("setDataPrefs", JSON.stringify({ cleanup_days: v ? +v : null })); if (x.ok) { S.data.settings = x.data; drawData(anchor); } })),
    h("div", { class: "pt sub2", text: `휴지통 ${D.trash.length}개 — ${prefs.trash_days || 7}일 뒤 자동으로 비움` }),
    ...D.trash.slice(0, 6).map((t) => h("div", { class: "darea" }, h("div", {}, h("b", { text: t.title || t.run_id || t.name }), h("span", { text: t.trashed_at ? ago(t.trashed_at) : "" })),
      h("button", { class: "btn quiet sm", onclick: async () => { const x = await call("restoreRun", t.name); if (x.ok) { toast("되살렸습니다"); refresh(); dataPop(anchor); } } }, "되살리기"))),
    D.trash.length ? h("div", { class: "droot" }, h("span"), h("button", { class: "btn sm", onclick: async () => {
      if (!confirm("휴지통을 비울까요? 되돌릴 수 없습니다.")) return; const x = await call("emptyTrash"); if (x.ok) { toast("비웠습니다"); dataPop(anchor); } } }, "휴지통 비우기")) : null) });
  return pop;
}

// 실행 하나의 메뉴 — 이름 바꾸기, 목록에서 숨기기
function runMenu(anchor, r) {
  const name = h("input", { class: "pin", value: r.name || "", onkeydown: async (e) => {
    if (e.key === "Enter") { const x = await call("renameRun", r.id, e.target.value); if (x.ok) { closePop(); refresh(); } } } });
  popover(anchor, { width: 260, content: h("div", { class: "optpop" }, h("div", { class: "pt", text: "이름 바꾸기 (Enter)" }), h("div", { class: "pf", style: "border:none;margin:0" }, name),
    h("button", { class: "pi", onclick: async () => { closePop(); const x = await call("archiveRun", r.id, !r.archived); if (x.ok) { if (!r.archived && S.current === r.id) goHome(); refresh(); } } },
      h("span", { class: "pl" }, h("b", { text: r.archived ? "다시 보이기" : "목록에서 숨기기" })), h("span", { class: "pd", text: r.archived ? "" : "기록은 그대로 남고 '숨긴 항목'에서 다시 볼 수 있습니다" })),
    h("button", { class: "pi danger", onclick: async () => { closePop();
        if (!confirm(`'${r.name || "프로젝트"}' 실행을 휴지통으로 옮길까요?

대화 흐름과 체크포인트가 함께 옮겨지고, 승인·검증 원장은 프로젝트에 남습니다. 저장 공간에서 되살릴 수 있습니다.`)) return;
        const x = await call("trashRun", r.id); if (x.ok) { toast("휴지통으로 옮겼습니다"); if (S.current === r.id) goHome(); refresh(); } } },
      h("span", { class: "pl" }, h("b", { text: "휴지통으로" })), h("span", { class: "pd", text: "저장 공간에서 되살리거나 비울 수 있습니다" }))) });
  setTimeout(() => name.focus(), 0);
}

// ---------------------------------------------------------------- 사이드바
function renderSide() {
  const sig = JSON.stringify([S.runs, S.current, S.agents, S.showHidden]);
  if (sig === S.sig.side) return; S.sig.side = sig;
  const box = $("#runs"); box.replaceChildren();
  const shown = S.runs.filter((r) => !r.archived), hidden = S.runs.filter((r) => r.archived);
  const active = shown.filter((r) => r.status !== "완료"), done = shown.filter((r) => r.status === "완료");
  const item = (r) => {
    const more = h("button", { class: "more", title: "이름 바꾸기 · 숨기기", onclick: (e) => { e.stopPropagation(); runMenu(e.currentTarget, r); } }, "⋯");
    const el = h("div", { class: "run" + (r.id === S.current ? " on" : ""), onclick: () => openRun(r.id),
      oncontextmenu: (e) => { e.preventDefault(); runMenu(more, r); } },
      h("span", { class: "t", text: r.name || "프로젝트" }), h("span", { class: "dot tone-" + r.tone }),
      h("span", { class: "m", text: `${r.mode_text} · ${r.status}` }), more);
    return el;
  };
  if (active.length) box.append(h("div", { class: "group", text: "진행 중" }), ...active.map(item));
  if (done.length) box.append(h("div", { class: "group", text: "완료" }), ...done.map(item));
  if (hidden.length) box.append(h("div", { class: "group toggle", onclick: () => { S.showHidden = !S.showHidden; S.sig.side = ""; renderSide(); } },
    `숨긴 항목 ${hidden.length} ${S.showHidden ? "▾" : "▸"}`), ...(S.showHidden ? hidden.map(item) : []));
  if (!S.runs.length) box.append(h("div", { class: "group", text: "프로젝트" }), h("div", { class: "empty", text: "아직 없습니다" }));
  const ag = $("#agents"); ag.replaceChildren();
  for (const a of S.agents) {
    if (!a.exe && a.id !== "claude") continue;
    ag.append(h("div", { class: "agent" + (a.ready ? " ok" : ""), title: a.hint || a.detail || "" }, h("i"),
      `${a.name.replace(" (실험적)", "")} ${a.ready ? "연결됨" : a.ready === null && a.exe ? "확인 중" : a.exe ? "로그인 필요" : "없음"}`,
      !a.ready && a.login ? h("button", { onclick: () => call("loginAgent", a.id).then((r) => r.ok && toast("터미널에서 로그인을 마쳐 주세요")) }, "로그인") : null));
  }
}

// ---------------------------------------------------------------- 홈
const EXAMPLES = [
  "주문 수량을 받아 총액을 계산하는 API. 개당 2,000원, 10개 이상이면 10% 할인, 수량이 0 이하면 400으로 거절.",
  "상품 목록을 CSV 로 내려받는 엔드포인트. 카테고리·가격 범위로 거르고, 1만 건이 넘으면 나눠서.",
  "검색어 자동완성: 입력 앞부분으로 인기 검색어 10개, 초성 검색 지원.",
];
// 처음 한 번: 시작 메뉴(검색)·바탕화면에서 앱을 열 수 있게
function shortcutBanner() {
  const sc = S.shortcut;
  if (!sc || !sc.supported || sc.dismissed || (sc.installed && sc.installed.start)) return null;
  const add = async (where) => { const r = await call("installShortcut", where); if (r.ok) { toast("바로가기를 만들었습니다 — 시작 메뉴 검색에서 beom 으로 찾을 수 있어요"); S.shortcut = (await call("shortcutStatus")).data; renderHome(); } };
  return h("div", { class: "banner" }, ico("book"),
    h("div", {}, h("b", { text: "검색이나 바탕화면에서 바로 열 수 있게 할까요?" }), h("span", { text: "시작 메뉴와 바탕화면에 바로가기를 만듭니다. 언제든 지울 수 있습니다." })),
    h("button", { class: "btn quiet sm", onclick: async () => { await call("dismissShortcut"); S.shortcut.dismissed = true; renderHome(); } }, "닫기"),
    h("button", { class: "btn sm", onclick: () => add("start") }, "시작 메뉴만"),
    h("button", { class: "btn primary sm", onclick: () => add("start,desktop") }, "둘 다 만들기"));
}
// 첨부 — 파일(docx·pptx·xlsx·hwpx 는 글로 바꿔 함께 줌)이나 링크(공개 페이지는 글로 저장)
function attachPop(anchor, d) {
  const add = (items) => { d.attachments = [...new Set([...(d.attachments || []), ...items])]; closePop(); renderHome(); };
  const link = h("input", { class: "pin", placeholder: "https://… 문서·페이지 주소 붙여넣고 Enter",
    onkeydown: (e) => { if (e.key === "Enter" && /^https?:\/\//.test(e.target.value.trim())) add([e.target.value.trim()]); } });
  popover(anchor, { width: 340, content: h("div", { class: "optpop" },
    h("div", { class: "pt", text: "기획서·문서 첨부" }),
    h("button", { class: "pi", onclick: async () => { const r = await call("pickFiles"); if (r.ok && r.data.length) add(r.data); } },
      h("span", { class: "pl" }, h("b", { text: "파일 선택" })), h("span", { class: "pd", text: "md · txt · pdf · docx · pptx · xlsx · hwpx · 이미지 — 오피스·한글 문서는 글로 바꿔 함께 넘깁니다" })),
    h("div", { class: "pt sub2", text: "링크" }),
    h("div", { class: "pf", style: "border:none;margin:0;padding-top:0" }, link),
    h("div", { class: "pnote", text: "공개 페이지는 내용을 저장해 넘기고, 로그인이 필요한 페이지(컨플루언스 등)는 링크만 넘깁니다 — 이때는 페이지를 내보내 파일로 첨부하면 확실합니다." })) });
  setTimeout(() => link.focus(), 0);
}
const PLAN_EXAMPLES = [
  "동네 카페 단골 적립 앱. 사장님은 태블릿으로 적립, 손님은 폰 번호로 확인.",
  "사내 회의실 예약 웹. 겹치면 막고, 슬랙으로 알림.",
  "쇼핑몰 관리자에서 상품 일괄 수정 화면. 엑셀 업로드·미리보기·되돌리기.",
];
function renderHome() {
  document.getElementById("app").classList.add("home");
  const m = $("#main"); m.replaceChildren();
  const d = S.draft, req = S.mode === "request";
  const PH = { request: "만들 기능을 적어 주세요 — 무엇을 받아 무엇을 돌려주는지, 예외는 어떻게 할지까지 적을수록 정확해집니다. 기획서를 첨부해도 됩니다.",
    oss: "가져올 기능과 바꿀 점을 적어 주세요 — 예) 대화형 검색을 Rust/Axum 으로, 우리 OpenSearch 로",
    plan: "만들고 싶은 것을 편하게 적어 주세요 — 누가 쓰는지, 왜 필요한지, 떠오르는 화면까지. 참고 문서·링크를 붙여도 됩니다.",
    free: "에이전트에게 바로 시킬 일을 적어 주세요 — 우리 절차 없이 그대로 전달합니다." };
  const ta = h("textarea", { id: "homeText", placeholder: PH[S.mode] || PH.request, oninput: (e) => { d.request = e.target.value; } });
  ta.value = d.request;
  ta.addEventListener("input", () => { const b = document.querySelector(".homebox .send"); if (b && req) b.disabled = S.preparing || (!d.request.trim() && !(d.attachments || []).length); });
  const folder = (key, label, hint) => h("span", { class: "field" + (d[key] ? "" : " unset"), title: d[key] || hint,
      onclick: async () => { const r = await call("pickFolder"); if (r.ok && r.data) { d[key] = r.data; renderHome(); } } },
    h("span", { class: "fold" }), label, h("b", { text: d[key] || hint }));
  d.policy = d.policy || { ...S.policy };
  const mode = (id, n, title, body, flow) => h("div", { class: "mode" + (S.mode === id ? " on" : ""), onclick: () => { S.mode = id; renderHome(); } },
    h("span", { class: "n", text: n }), h("b", { text: title }), h("span", { text: body }), h("span", { class: "flow", text: flow }));
  const srcFields = S.mode !== "oss" ? null : h("div", { class: "srcrow" },
    h("label", { class: "sf" }, h("span", { text: "원본" }), h("input", { placeholder: "github.com/조직/저장소 또는 로컬 경로", value: d.source,
      oninput: (e) => { d.source = e.target.value; } })),
    h("label", { class: "sf" }, h("span", { text: "기능" }), h("input", { placeholder: "(선택) 비워 두면 기능 목록을 보여 주고 고르게 합니다", value: d.feature,
      oninput: (e) => { d.feature = e.target.value; } })));
  const brainBtn = pill("agent", "에이전트 · 모델 · 노력", brainLabel(d.agent, d.model, d.effort), (b) => brainPop(b, () => d,
    (patch) => { Object.assign(d, patch); const bb = document.querySelector(".toolbar .pill.brain .pv"); if (bb) bb.textContent = brainLabel(d.agent, d.model, d.effort); }, true), "brain");
  const atts = (d.attachments || []).length ? h("div", { class: "atts" }, ...(d.attachments || []).map((f) => h("span", { class: "att", title: f },
    ico(/^https?:/.test(f) ? "book" : "clip"), /^https?:/.test(f) ? f.replace(/^https?:\/\//, "").slice(0, 40) : f.split(/[\\/]/).pop(), h("button", { title: "빼기", onclick: () => { d.attachments = d.attachments.filter((x) => x !== f); renderHome(); } }, "×")))) : null;
  const fields = h("div", { class: "toolbar" },
    h("div", { class: "tl" },
      h("button", { class: "iconbtn", title: "기획서·문서·링크 첨부", onclick: (e) => { e.stopPropagation(); attachPop(e.currentTarget, d); } }, ico("clip")),
      folder("target_root", { request: "만들 곳", oss: "옮길 곳", plan: "저장할 곳", free: "작업 폴더" }[S.mode] || "폴더",
        S.mode === "plan" ? "선택 안 하면 자동" : "폴더 선택"),
      req ? folder("context_dir", "참고", "저장소 선택") : null,
      pill("shield", "권한", modeLabel(d.policy), (b) => permMenu(b, d.policy, (p) => { d.policy = p; renderHome(); }, () => optionsPop(b, true))),
      h("button", { class: "iconbtn", title: "실행 옵션 (라운드·계획 승인·인증·세부 권한)", onclick: (e) => { e.stopPropagation(); optionsPop(e.currentTarget, false); } }, ico("sliders"))),
    h("div", { class: "tr" }, brainBtn,
      h("button", { class: "send", disabled: S.preparing || !d.request.trim() && S.mode !== "oss" && !(d.attachments || []).length, title: "시작 (Ctrl+Enter)", onclick: create },
        S.preparing ? h("span", { class: "spin" }) : ico("up"))));
  const wrap = h("div", { class: "homewrap" }, h("div", { class: "homein" },
    shortcutBanner(),
    h("div", { class: "eyebrow", text: "NEW TASK" }),
    h("h1", { text: "새 작업" }),
    h("div", { class: "modes four" },
      mode("request", "01", "요청사항으로 만들기", "기준과 계획을 처음에 한 번 확인하면, 그 기준대로 될 때까지 만들고 검증합니다.",
           "요구사항 → 합격 기준 → 계획 → 구현 → 검증"),
      mode("oss", "02", "오픈소스에서 가져오기", "원본 저장소의 기능을 내 프로젝트로 옮기고, 원본과 같은 동작인지 비교합니다.",
           "분석 → 범위 → 원본 기록 → 구현 → 비교"),
      mode("plan", "03", "기획하기", "아이디어를 바로 만들 수 있는 기획서로 — 질문 몇 개로 빠르게. 다 되면 그대로 만들기로 넘깁니다.",
           "질문 → 기획서 → 다듬기 → 만들기로"),
      mode("free", "04", "자유 대화", "우리 절차 없이 Claude Code·Codex 를 그대로 — 폴더에서 대화하며 작업합니다.",
           "메시지 → 작업 → 메시지 …")),
    h("div", { class: "homebox" }, h("div", { class: "box" }, srcFields, ta, atts, fields)),
    h("div", { class: "homehint" }, h("span", { text: `${agentName(d.agent)} · ${modelLabel(d.agent, d.model)}${d.effort ? " · " + effortLabel(d.agent, d.model, d.effort) : ""} · 권한 ${modeLabel(d.policy)} · ${+d.max_rounds ? `최대 ${d.max_rounds}회` : "끝날 때까지"} · ${(d.confirm_at || "start") === "start" ? "처음에 한 번 확인" : "마지막에 한 번 확인"}` }),
      h("span", { class: "k", text: "Ctrl Enter 시작" })),
    (req || S.mode === "plan") && !d.request ? h("div", { class: "examples" }, ...(S.mode === "plan" ? PLAN_EXAMPLES : EXAMPLES).map((x) => h("div", { class: "ex", onclick: () => { d.request = x; renderHome(); } }, x))) : null,
    S.runs.length ? h("div", { class: "recent" }, h("h4", { text: "RECENT" }),
      h("div", { class: "rtable" }, ...S.runs.slice(0, 6).map((r) => h("div", { class: "rrow", onclick: () => openRun(r.id) },
        h("span", { class: "dot tone-" + r.tone }), h("span", { class: "t", text: r.name }), h("span", { class: "m", text: r.mode_text }),
        h("span", { class: "m", text: r.status }), h("span", { class: "m", text: ago(r.created_at) }))))) : null));
  m.append(wrap);
  $("#right").replaceChildren();
  setTimeout(() => ta.focus(), 0);
}
// 실행 옵션 — 홈에서는 초안(S.draft)을, 실행 화면에서는 그 실행을 바꾼다
function optionsPop(anchor, focusPerm, run) {
  const d = run ? { max_rounds: run.max_rounds, plan_approval: run.plan_approval ?? S.detail.plan_approval, confirm_at: run.confirm_at ?? S.detail.confirm_at,
    policy: { ...(run.policy || S.detail.policy) }, auth: run.auth } : S.draft;
  const save = (patch) => {
    if (run) call("setRunOptions", run.id, JSON.stringify(patch)).then(async (r) => {
      if (!r.ok) return;
      const next = { ...run, ...r.data };
      await refresh();
      optionsPop(document.querySelector("#cbar .iconbtn") || anchor, focusPerm, next);
    });
    else { Object.assign(S.draft, patch); renderHome(); optionsPop(document.querySelector(".iconbtn") || anchor, focusPerm); }
  };
  const agent = S.agents.find((a) => a.id === (run ? run.agent : d.agent));
  const pop = popover(anchor, { width: 380, content: h("div", { class: "optpop" },
    h("div", { class: "pt", text: "실행 옵션" }),
    h("div", { class: "orow" }, h("div", {}, h("b", { text: "반복 횟수" }), h("span", { text: +d.max_rounds
        ? "에이전트를 이 횟수까지 다시 부릅니다" : "완료 조건을 채울 때까지 반복 — 진전이 없으면 스스로 멈춥니다" })),
      h("div", { style: "display:flex;gap:6px;align-items:center" },
        toggle(+d.max_rounds > 0, (on) => save({ max_rounds: on ? 20 : 0 })),
        +d.max_rounds > 0 ? h("div", { class: "step" }, h("button", { onclick: (e) => { e.stopPropagation(); save({ max_rounds: Math.max(1, +d.max_rounds - 5) }); } }, "−"),
          h("span", { text: String(d.max_rounds) }), h("button", { onclick: (e) => { e.stopPropagation(); save({ max_rounds: Math.min(500, +d.max_rounds + 5) }); } }, "+")) : null)),
    h("div", { class: "orow" }, h("div", {}, h("b", { text: "사람 확인" }), h("span", { text: (d.confirm_at || "start") === "start"
        ? "처음에 기준과 계획을 한 번에 확인하면 끝날 때까지 맡깁니다" : "멈추지 않고 만들고 검증한 뒤, 마지막에 한 번 확인합니다" })),
      segmented([["start", "처음에 한 번"], ["end", "마지막에 한 번"]], d.confirm_at || "start", (v) => save({ confirm_at: v }))),
    (d.confirm_at || "start") === "start" ? h("div", { class: "orow" }, h("div", {}, h("b", { text: "계획도 함께 확인" }), h("span", { text: "기준과 함께 구현 계획도 처음에 확인" })),
      toggle(d.plan_approval === true || d.plan_approval === "true", (v) => save({ plan_approval: v }))) : null,
    run ? null : h("div", { class: "orow" }, h("div", {}, h("b", { text: "요금" }), h("span", { text: (d.auth || "subscription") === "subscription"
        ? "구독 요금제(Claude Pro·Max, ChatGPT Plus·Pro)로 로그인한 그대로 — 요금제 한도 안에서 추가 비용 없음"
        : "개발자 콘솔에서 발급한 API 키(환경 변수) — 쓴 만큼 따로 청구. 할 수 있는 일은 같습니다" })),
      segmented([["subscription", "구독 로그인"], ["api_key", "API 키 (사용량 청구)"]], d.auth || "subscription", (v) => save({ auth: v }))),
    h("div", { class: "pt sub2", text: "권한 — 작업 종류별" }),
    agent && agent.note ? h("div", { class: "pnote", text: agent.note }) : null,
    policyRows(d.policy, (p) => save({ policy: p }))) });
  if (focusPerm) pop.querySelector(".prow-list").scrollIntoView({ block: "nearest" });
}
async function create() {
  const d = S.draft;
  const payload = { mode: S.mode, request: d.request, target_root: d.target_root, context_dir: d.context_dir, source: d.source,
    feature: d.feature, agent: d.agent, model: d.model, effort: d.effort, confirm_at: d.confirm_at, attachments: d.attachments || [], max_rounds: d.max_rounds, auth: d.auth,
    plan_approval: d.plan_approval === true || d.plan_approval === "true", policy: d.policy || S.policy,
    name: (d.request || d.feature || "새 프로젝트").split(/[.\n]/)[0].slice(0, 28) };
  S.preparing = true; renderHome();
  const r = await call("createRun", JSON.stringify(payload));
  if (!r.ok) { S.preparing = false; renderHome(); }
}

// ---------------------------------------------------------------- 프로젝트
async function openRun(id) {
  S.current = id; S.detail = null; S.changes = null; S.file = null; closePop(); S.edit = null; S.sig = { feed: "", right: "", side: "", cbar: "" };
  const msg = $("#msg"); if (msg) { msg.value = ""; msg.style.height = "auto"; }
  document.getElementById("app").classList.remove("home");
  await refresh();
}
function renderRun() {
  const D = S.detail; if (!D) return;
  const run = D.run, m = $("#main");
  if (!m.querySelector(".feed")) {
    m.replaceChildren(
      h("div", { class: "head", id: "head" }), h("div", { class: "meter", id: "meter" }), h("div", { id: "activity" }),
      h("div", { class: "feed", id: "feed" }, h("div", { class: "col", id: "col" })),
      h("div", { class: "composer" }, h("div", { class: "box" },
        h("textarea", { id: "msg", rows: 1, placeholder: "에이전트에게 지시하거나 고칠 점을 적어 주세요",
          oninput: (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(180, e.target.scrollHeight) + "px"; },
          onkeydown: (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(); } },
          onkeyup: () => updateSendMode() }),
        h("div", { class: "bar", id: "cbar" }))));
  }
  $("#head").replaceChildren(...[
    h("span", { class: "crumb", text: D.mode_text + " /" }), h("h1", { text: run.name || "프로젝트" }),
    h("span", { class: "state " + D.tone }, h("i"), D.status),
    h("span", { class: "path", text: run.target_root, title: "폴더 열기", onclick: () => call("openFolder", run.target_root) }),
  ].filter(Boolean));
  const n = D.stages.length, cols = `repeat(${n}, 1fr)`;
  $("#meter").replaceChildren(
    h("div", { class: "bars", style: `grid-template-columns:${cols}` }, ...D.stages.map((_, i) => h("i", { class: i < D.stage ? "done" : i === D.stage ? "now" : "" }))),
    h("div", { class: "labels", style: `grid-template-columns:${cols}` }, ...D.stages.map((s, i) => h("span", { class: i < D.stage ? "done" : i === D.stage ? "now" : "", text: `${String(i + 1).padStart(2, "0")} ${s}` }))));
  const act = $("#activity"); act.replaceChildren();
  if (D.running) {
    if (!S.startedAt) S.startedAt = Date.now();
    const sec = Math.floor((Date.now() - S.startedAt) / 1000);
    act.append(h("div", { class: "activity" }, h("span", { class: "pulse" }),
      (D.activity && D.activity.text) || `라운드 ${run.round} 진행 중`,
      h("span", { class: "el", text: `${agentName(run.agent)} · ${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}` })));
  } else S.startedAt = null;
  const cbarSig = JSON.stringify([run.agent, run.model, run.effort, run.round, run.max_rounds, run.cost_usd, D.policy, D.running, run.status, run.accepted]);
  if (cbarSig !== S.sig.cbar) {  // 메뉴가 열린 채로 1.5초마다 다시 그려지지 않게
    S.sig.cbar = cbarSig;
    const opt = (patch) => call("setRunOptions", run.id, JSON.stringify(patch)).then((r) => r.ok && (toast("바꿨습니다 — 다음 라운드부터 적용"), refresh()));
    const cur = { agent: run.agent, model: run.model || "", effort: run.effort || "" };
    const resumable = !D.running && !(run.status === "done" && run.accepted);
    $("#cbar").replaceChildren(
      h("div", { class: "tl" },
        pill("shield", "권한", modeLabel(D.policy), (b) => permMenu(b, D.policy, (p) => opt({ policy: p }), () => optionsPop(b, true, run))),
        h("button", { class: "iconbtn", title: "실행 옵션 (라운드·계획 승인·세부 권한)", onclick: (e) => { e.stopPropagation(); optionsPop(e.currentTarget, false, { ...run, policy: D.policy, plan_approval: D.plan_approval }); } }, ico("sliders")),
        h("span", { class: "meta", text: `${run.round || 0}회째${run.max_rounds ? ` / ${run.max_rounds}` : ""}` + (run.cost_usd ? ` · ${fmtMoney(run.cost_usd)}` : "") })),
      h("div", { class: "tr" },
        resumable && run.round ? h("button", { class: "btn quiet sm", title: "메시지 없이 이어서 진행", onclick: startRun }, ico("play"), "이어서") : null,
        pill("agent", "모델 · 노력 (다음 라운드부터)", brainLabel(run.agent, run.model, run.effort), (b) => brainPop(b, () => cur,
          (patch) => { Object.assign(cur, patch); opt(patch); }, false), "brain"),
        // Claude 데스크톱처럼: 진행 중이고 입력이 비었으면 멈춤, 아니면 보내기
        h("button", { class: "send" + (D.running ? " stopmode" : ""), title: D.running ? "멈추기 (Esc) — 입력하면 보내기로 바뀝니다" : (run.round ? "보내기 — 보내면 바로 이어서 진행" : "시작"),
          disabled: !D.running && !run.round ? false : false,
          onclick: () => { const t = $("#msg"); if (t && t.value.trim()) sendMsg(); else if (D.running) call("stop", run.id).then(refresh); else startRun(); } },
          ico(D.running ? "stop" : "up"))));
    updateSendMode();
  }
  renderFeed();
  renderRight();
}
function updateSendMode() {
  const b = document.querySelector(".composer .send"); if (!b || !S.detail) return;
  const typed = !!($("#msg") && $("#msg").value.trim()), running = S.detail.running;
  b.classList.toggle("stopmode", running && !typed);
  b.replaceChildren(ico(running && !typed ? "stop" : "up"));
  const r = S.detail.run;
  b.disabled = !typed && !running && (r.status === "done" && r.accepted);
}
// 쓰던 에이전트가 연결돼 있지 않으면 연결된 것으로 (Claude 고정이 아니라)
function pickReadyAgent() {
  const d = S.draft, cur = S.agents.find((a) => a.id === d.agent);
  if (S.agentPicked || (cur && cur.ready)) return;
  const ready = S.agents.find((a) => a.ready);
  if (ready && ready.id !== d.agent) { d.agent = ready.id; d.model = ""; d.effort = ""; }
}
function agentName(id) { const a = S.agents.find((x) => x.id === id); return a ? a.name.replace(" (실험적)", "") : (id || "Claude Code"); }
async function startRun() {
  const msg = $("#msg"); if (msg && msg.value.trim()) await sendMsg();
  const r = await call("start", S.current); if (r.ok) { S.startedAt = Date.now(); refresh(); }
}
async function sendMsg() {
  const t = $("#msg"); const text = t.value.trim(); if (!text) return;
  const r = await call("sendMessage", S.current, text);
  if (r.ok && !S.detail.running && !S.edit) {  // 멈췄거나 끝난 실행에 보내면 대화처럼 바로 이어서 진행
    t.value = ""; t.style.height = "auto";
    const st = await call("start", S.current); if (st.ok) { S.startedAt = Date.now(); toast("보냈습니다 — 이어서 진행합니다"); refresh(); }
    return;
  }
  if (r.ok) { t.value = ""; t.style.height = "auto"; updateSendMode(); toast(S.detail.running ? "다음 라운드에 전달합니다" : "보냈습니다. 진행을 누르면 반영합니다"); refresh(); }
}

function renderFeed() {
  const D = S.detail;
  if (S.edit && S.edit.drawn) return;  // 편집 중에는 다시 그리지 않는다 (입력 중인 내용이 날아가지 않게)
  const sig = JSON.stringify([D.feed, D.reviews, D.asks, D.plan, D.handoff, D.run.status, D.run.accepted, D.running, S.edit && S.edit.kind]);
  if (sig === S.sig.feed) return; S.sig.feed = sig;
  if (S.edit) S.edit.drawn = true;
  const col = $("#col"), feed = $("#feed");
  const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
  col.replaceChildren();
  for (const it of D.feed) col.append(feedItem(it));
  const cs = cards();
  for (const c of cs) col.append(row(null, "decision", c));
  if (!D.feed.length && !D.running) col.append(h("div", { class: "empty", text: "시작하면 에이전트의 작업이 여기에 시간순으로 쌓입니다.\n결정이 필요할 때만 멈추고 묻습니다." }));
  if (atBottom || S.sig.first !== D.run.id) { feed.scrollTop = feed.scrollHeight; S.sig.first = D.run.id; }
}
function hhmm(iso) {
  if (!iso) return "";
  const d = new Date(iso); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
// 에이전트 메시지 — `코드` 와 줄바꿈만 살린다 (HTML 은 그대로 글자로)
function richText(text) {
  const el = h("div", { class: "msg" });
  String(text || "").split(/(`[^`\n]+`)/).forEach((part) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) el.append(h("code", { text: part.slice(1, -1) }));
    else el.append(document.createTextNode(part));
  });
  return el;
}
function row(at, node, content) {
  return h("div", { class: "row" }, h("time", { text: hhmm(at) }), h("span", { class: "node " + (node || "") }), h("div", { class: "body" }, content));
}
function feedItem(it) {
  switch (it.type) {
    case "request": return row(it.at, "message", h("div", { class: "req" }, h("div", { class: "who", text: S.detail.run.mode === "oss" ? "가져올 기능" : "요청" }),
      h("div", { class: "body", text: it.text })));
    case "user": return row(it.at, "user", h("div", { class: "user" }, h("div", { class: "who", text: "추가 지시" }), it.text));
    case "divider": return h("div", { class: "round" }, h("b", { text: it.text.replace("라운드 ", "R") }), it.at ? hhmm(it.at) : "");
    case "message": return row(it.at, "message", richText(it.text));
    case "tools": {
      const max = 6, el = h("div", { class: "tools" }, ...it.items.slice(0, max).map((x) => h("span", { text: x, title: x })));
      if (it.items.length > max) {
        const more = h("span", { class: "more", text: `+${it.items.length - max}` });
        more.onclick = () => { more.remove(); el.append(...it.items.slice(max).map((x) => h("span", { text: x, title: x }))); };
        el.append(more);
      }
      return row(it.at, "", el);
    }
    case "checkpoint": return row(it.at, "", h("div", { class: "cp" }, ico("book"), h("span", { text: `체크포인트 · ${it.label}` }),
      h("button", { onclick: () => restore(it.id, it.label) }, "이 시점으로 되돌리기")));
    default: return row(it.at, it.tone === "ok" ? "ok" : it.tone === "fail" ? "fail" : "", h("div", { class: "note " + (it.tone || ""), text: it.text }));
  }
}
async function restore(id, label) {
  if (!confirm(`'${label}' 시점으로 대상 폴더의 파일을 되돌립니다.\n\n그 뒤에 생긴 파일은 지워지고 바뀐 파일은 원래대로 돌아갑니다. 대화 기록과 원장(승인·검증 기록)은 그대로 남습니다. 되돌리기 전 상태도 체크포인트로 남아 다시 되돌릴 수 있습니다.`)) return;
  const r = await call("restore", S.current, id); if (r.ok) { toast("되돌렸습니다"); refresh(); }
}
function cards() {
  const D = S.detail, out = [];
  if (D.reviews && D.reviews.count) out.push(reviewCard(D.reviews));
  const plan = D.plan;
  // 기준과 계획은 함께 승인하므로 둘 다 보여 준다 — 보지 않은 계획(디자인 방향 포함)이 승인되지 않게
  if (plan && plan.steps && plan.steps.length && !plan.approved) out.push(planCard(plan));
  for (const a of D.asks) out.push(a.kind === "permission" ? permCard(a) : askCard(a));
  if (D.handoff) out.push(handoffCard(D.handoff));
  for (const c of out) if (!c.classList.contains("done")) c.classList.add("decide");
  return out;
}
// 케이스가 무엇을 넣어 보는지 — 입력이 안 보이면 기대값만으로는 기준이 맞는지 판단할 수 없다
function inputText(i) {
  if (!i || !Object.keys(i).length) return "(입력 없음)";
  if (i.screen) { const sc = typeof i.screen === "object" ? i.screen : { path: i.screen };
    return `화면 ${sc.file || sc.path || "/"}${sc.mobile ? " · 모바일" : ""}`; }
  if (i.argv) return i.argv.length ? i.argv.map((a) => (/\s/.test(a) || a === "" ? JSON.stringify(a) : a)).join(" ") : "(인자 없음)";
  if (i.path || i.method) return `${i.method || "GET"} ${i.path || "/"}` + (i.json !== undefined ? " " + JSON.stringify(i.json) : "");
  if (i.call) return `${Object.values(i.call)[0] || "호출"}(${(i.args || []).map((a) => JSON.stringify(a)).join(", ")})`;
  if (i.stdin !== undefined) return "stdin " + JSON.stringify(i.stdin);
  return JSON.stringify(i);
}
// ---------------------------------------------------------------- 직접 고치기 (요구사항·합격 기준·계획)
function argvText(a) { return (a || []).map((x) => (/[\s"']/.test(x) || x === "" ? JSON.stringify(x) : x)).join(" "); }
function parseArgv(t) {
  const out = []; const re = /"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+)/g; let m;
  while ((m = re.exec(t))) out.push(m[1] !== undefined ? JSON.parse('"' + m[1] + '"') : m[2] !== undefined ? m[2] : m[3]);
  return out;
}
const INPUT_KEYS = ["argv", "stdin", "method", "path", "json", "body", "headers", "call", "args", "kwargs"];
function caseToRow(c) {
  const input = {}; for (const k of INPUT_KEYS) if (k in c) input[k] = c[k];
  const cliOnly = Object.keys(input).every((k) => k === "argv");
  const rest = {}; for (const [k, v] of Object.entries(c)) if (!INPUT_KEYS.includes(k) && !["name", "req", "expect", "description"].includes(k)) rest[k] = v;
  return { name: c.name || "", desc: c.description || "", cli: cliOnly, input: cliOnly ? argvText(input.argv) : JSON.stringify(input),
    expect: JSON.stringify(c.expect || {}), req: [].concat(c.req || []).join(" "), rest };
}
function rowToCase(r, i) {
  const name = r.name.trim() || `case${i + 1}`;
  let input, expect;
  try { input = r.cli ? { argv: parseArgv(r.input) } : JSON.parse(r.input || "{}"); } catch { throw new Error(`${name}: 입력이 JSON 형식이 아닙니다`); }
  try { expect = JSON.parse(r.expect || "{}"); } catch { throw new Error(`${name}: 기대값이 JSON 형식이 아닙니다`); }
  const req = r.req.split(/[\s,]+/).filter(Boolean);
  return { name, ...(r.desc && r.desc.trim() ? { description: r.desc.trim() } : {}), ...input, ...r.rest, req: req.length === 1 ? req[0] : req, expect };
}
async function startEdit(kind) {
  closePop();
  if (kind === "spec") {
    const r = await call("specForEdit", S.current); if (!r.ok) return;
    S.edit = { kind, reqs: r.data.requirements.map((x) => ({ ...x })), cases: r.data.cases.map(caseToRow) };
  } else {
    const p = S.detail.plan;
    S.edit = { kind, summary: p.summary || "", steps: p.steps.map((x) => ({ title: x.title, files: (x.files || []).join(", "),
      commands: (x.commands || []).join("\n"), requirements: x.requirements || [], status: x.status })) };
  }
  S.sig.feed = ""; renderFeed();
}
function cancelEdit() { S.edit = null; S.sig.feed = ""; renderFeed(); }
async function saveEdit(approve) {
  const E = S.edit; let r;
  try {
    if (E.kind === "spec") {
      const reqs = E.reqs.filter((x) => x.text.trim()).map((x) => (x.id ? { id: x.id, text: x.text.trim() } : { text: x.text.trim() }));
      r = await call("saveSpec", S.current, JSON.stringify({ requirements: reqs, cases: E.cases.map(rowToCase) }));
    } else {
      r = await call("savePlan", S.current, JSON.stringify({ summary: E.summary, steps: E.steps.map((x) => ({ title: x.title,
        files: x.files.split(",").map((f) => f.trim()).filter(Boolean), commands: x.commands.split("\n").map((c) => c.trim()).filter(Boolean),
        requirements: x.requirements, status: x.status })) }));
    }
  } catch (err) { toast(err.message); return; }
  if (!r.ok) return;
  S.edit = null;
  if (approve) {
    const a = E.kind === "spec" ? await call("approveReviews", S.current, r.data.review_sig || "") : await call("approvePlan", S.current, r.data.plan_sig || "");
    if (a.ok) toast(E.kind === "spec" ? "고친 내용으로 승인했습니다" : "고친 계획으로 승인했습니다");
  } else toast("저장했습니다 — 확인 후 승인해 주세요");
  S.sig.feed = ""; refresh();
}
function editField(obj, key, attrs) {
  const tag = attrs && attrs.multiline ? "textarea" : "input";
  const el = h(tag, { class: "ef" + (attrs && attrs.mono ? " mono" : ""), placeholder: (attrs && attrs.ph) || "", rows: attrs && attrs.multiline ? 2 : null,
    oninput: (e) => { obj[key] = e.target.value; } });
  el.value = obj[key] ?? "";
  return el;
}
function editFooter() {
  return h("div", { class: "cf" }, h("span", { class: "hint", text: "고친 내용은 사용자 작성으로 원장에 남고, 에이전트에게도 알립니다" }),
    h("button", { class: "btn quiet", onclick: cancelEdit }, "취소"),
    h("button", { class: "btn", onclick: () => saveEdit(false) }, "저장"),
    h("button", { class: "btn primary", onclick: () => saveEdit(true) }, "저장하고 승인"));
}
function specEditCard() {
  const E = S.edit;
  const redraw = () => { S.edit.drawn = false; S.sig.feed = ""; renderFeed(); };
  return h("div", { class: "card editing" },
    h("div", { class: "ch" }, h("h3", { text: "요구사항과 합격 기준 고치기" }), h("span", { class: "tag", text: "편집 중" })),
    h("div", { class: "sub", text: "요구사항" }),
    h("div", { class: "elist" }, ...E.reqs.map((r, i) => h("div", { class: "erow" },
      h("span", { class: "eid", text: r.id || "새" }), editField(r, "text", { ph: "요구사항 문장" }),
      h("button", { class: "ebtn", title: "빼기", onclick: () => { E.reqs.splice(i, 1); redraw(); } }, "×"))),
      h("button", { class: "add", onclick: () => { E.reqs.push({ text: "" }); redraw(); } }, "+ 요구사항 추가")),
    h("div", { class: "sub", text: "합격 기준 — 입력은 CLI 인자(공백으로 구분) 또는 JSON, 기대값은 JSON (status · body · equals · match)" }),
    h("div", { class: "elist" }, h("div", { class: "erow head" }, h("span", { text: "설명" }), h("span", { text: "입력" }), h("span", { text: "기대" }), h("span", { text: "요구사항" }), h("span")),
      ...E.cases.map((c, i) => h("div", { class: "erow case" },
        editField(c, "desc", { ph: "설명 (예: 수량 10개 — 할인 시작)" }), editField(c, "input", { mono: true, ph: c.cli ? "인자" : "{\"path\": \"/\"}" }),
        editField(c, "expect", { mono: true, ph: '{"status": 0}' }), editField(c, "req", { ph: "R1 R2" }),
        h("button", { class: "ebtn", title: "빼기", onclick: () => { E.cases.splice(i, 1); redraw(); } }, "×"))),
      h("button", { class: "add", onclick: () => { const last = E.cases[E.cases.length - 1];
        E.cases.push({ name: "", desc: "", cli: last ? last.cli : true, input: "", expect: last ? last.expect : '{"status": 0}', req: "", rest: {} }); redraw(); } }, "+ 케이스 추가")),
    editFooter());
}
function planEditCard() {
  const E = S.edit;
  const redraw = () => { S.edit.drawn = false; S.sig.feed = ""; renderFeed(); };
  const move = (i, d) => { const j = i + d; if (j < 0 || j >= E.steps.length) return; [E.steps[i], E.steps[j]] = [E.steps[j], E.steps[i]]; redraw(); };
  return h("div", { class: "card editing" },
    h("div", { class: "ch" }, h("h3", { text: "구현 계획 고치기" }), h("span", { class: "tag", text: "편집 중" })),
    h("div", { class: "epad" }, editField(E, "summary", { ph: "계획 요약", multiline: true })),
    h("div", { class: "elist" }, ...E.steps.map((st, i) => h("div", { class: "estep" },
      h("div", { class: "eh" }, h("span", { class: "eid", text: String(i + 1).padStart(2, "0") }), editField(st, "title", { ph: "단계 제목" }),
        h("button", { class: "ebtn", title: "위로", onclick: () => move(i, -1) }, "↑"), h("button", { class: "ebtn", title: "아래로", onclick: () => move(i, 1) }, "↓"),
        h("button", { class: "ebtn", title: "빼기", onclick: () => { E.steps.splice(i, 1); redraw(); } }, "×")),
      h("div", { class: "eb" }, editField(st, "files", { mono: true, ph: "바꿀 파일 (쉼표로 구분)" }),
        editField(st, "commands", { mono: true, ph: "실행할 명령 (한 줄에 하나)", multiline: true })))),
      h("button", { class: "add", onclick: () => { E.steps.push({ title: "", files: "", commands: "", requirements: [], status: "todo" }); redraw(); } }, "+ 단계 추가")),
    editFooter());
}
function reviewCard(R) {
  if (S.edit && S.edit.kind === "spec") return specEditCard();
  const rows = [];
  if (R.requirements.length) rows.push(h("table", { class: "tbl" }, ...R.requirements.map((r) => h("tr", {}, h("td", { class: "k", text: r.id }), h("td", { text: r.text })))));
  if (R.expectations.length) rows.push(h("div", { class: "sub", text: "합격 기준" }),
    h("table", { class: "tbl" }, h("tr", { class: "th" }, h("td", { text: "케이스" }), h("td", { text: "입력" }), h("td", { text: "기대" }), h("td", { text: "" })),
      ...R.expectations.map((e) => h("tr", {}, h("td", {}, e.description || e.case, e.description ? h("span", { class: "cid", text: e.case }) : null), h("td", { class: "c in", text: inputText(e.input) }),
      h("td", { class: "c", text: expectText(e.expect) }), h("td", { class: "r", text: (e.reqs || []).join(" ") })))));
  if (R.divergences.length) rows.push(h("div", { class: "sub", text: "원본과 다르게 두는 점" }),
    h("table", { class: "tbl" }, ...R.divergences.map((x) => h("tr", {}, h("td", { text: x.summary })))));
  if (R.omitted.length) rows.push(h("div", { class: "sub", text: "가져오지 않는 부분" }),
    h("table", { class: "tbl" }, ...R.omitted.map((x) => h("tr", {}, h("td", { text: x.summary })))));
  if (R.case_changes.length) rows.push(h("div", { class: "sub", text: "케이스 변경" }),
    h("table", { class: "tbl" }, ...R.case_changes.flatMap((x) => [
      (x && x.removed && x.removed.length) ? h("tr", {}, h("td", { class: "k", text: "빠짐" }), h("td", { text: x.removed.join(", ") + " — 이전 승인 기준에서 빠진 케이스" })) : null,
      (x && x.changed && x.changed.length) ? h("tr", {}, h("td", { class: "k", text: "바뀜" }), h("td", { text: x.changed.join(", ") + " — 기대값이 이전 승인과 다름" })) : null,
    ].filter(Boolean))));
  const spec = R.requirements.length || R.expectations.length;
  return h("div", { class: "card" },
    h("div", { class: "ch" }, h("h3", { text: spec ? "요구사항과 합격 기준" : "확인할 항목" }), h("span", { class: "tag", text: "확인 필요" })),
    h("div", { class: "desc", text: "이 내용이 완료 기준이 됩니다. 승인하면 이대로 만들고 검증하며, 나중에 바뀌면 다시 확인을 받습니다." }),
    ...rows,
    h("div", { class: "cf" }, h("span", { class: "hint", text: "직접 고치거나, 고칠 점을 입력창으로 보내세요" }),
      spec && S.detail.run.mode === "request" ? h("button", { class: "btn", onclick: () => startEdit("spec") }, "고치기") : null,
      h("button", { class: "btn primary", onclick: () => approveShown(bothPending() ? "both" : "reviews") },
        bothPending() ? "기준과 계획 승인" : "승인", h("span", { class: "k", text: "Ctrl Enter" }))));
}
function expectText(e) {
  if (!e) return "";
  const parts = [];
  if (e.visible) parts.push("보여야 함: " + e.visible.join(", "));
  if (e.selectors) parts.push("요소: " + e.selectors.join(", "));
  if (e.no_errors) parts.push("스크립트 오류 없음");
  if (e.status != null) parts.push(String(e.status));
  for (const [k, v] of Object.entries(e.equals || {})) parts.push(`${k} ${JSON.stringify(v)}`);
  for (const [k, v] of Object.entries(e.match || {})) parts.push(`${k} ~ /${v}/`);
  if (e.body !== undefined) parts.push("본문 " + JSON.stringify(e.body).slice(0, 80));
  return parts.join(" · ");
}
function planCard(plan) {
  if (S.edit && S.edit.kind === "plan") return planEditCard();
  return h("div", { class: "card" },
    h("div", { class: "ch" }, h("h3", { text: "구현 계획" }), h("span", { class: "tag", text: "확인 필요" })),
    plan.summary ? h("div", { class: "desc", text: plan.summary }) : null,
    plan.design ? h("div", { class: "design" }, h("div", { class: "sub", text: "디자인 방향 — 이 방향으로 화면을 만듭니다" }), renderMd(plan.design)) : null,
    h("div", { class: "steps-list" }, ...plan.steps.map((s) => h("div", { class: "s" }, h("span", { text: s.title }),
      (s.files || []).length || (s.commands || []).length ? h("span", { class: "f", text: [...(s.files || []), ...(s.commands || []).map((c) => "$ " + c)].join("   ") }) : null))),
    h("div", { class: "cf" }, h("span", { class: "hint", text: "직접 고치거나, 바꿀 점을 입력창으로 보내세요" }),
      h("button", { class: "btn", onclick: () => startEdit("plan") }, "고치기"),
      h("button", { class: "btn primary", onclick: () => approveShown(bothPending() ? "both" : "plan") },
        bothPending() ? "기준과 계획 승인" : "승인하고 구현 시작", h("span", { class: "k", text: "Ctrl Enter" }))));
}
function permCard(a) {
  const input = a.input || {};
  const what = (input.commands && input.commands.join("\n")) || input.command || input.file_path || input.url || JSON.stringify(input).slice(0, 300);
  const ans = (data) => call("answer", S.current, a.id, JSON.stringify(data)).then(refresh);
  return h("div", { class: "card" },
    h("div", { class: "ch" }, h("h3", { text: "작업 허락 요청" }), h("span", { class: "tag", text: S.kinds[a.permission_kind] || a.permission_kind || "" })),
    h("div", { class: "desc", text: `${a.tool || "도구"} — 규칙상 확인이 필요한 작업입니다.` }),
    h("div", { class: "cmd", text: what }),
    h("div", { class: "cf" }, h("span", { class: "hint", text: "Y 허용 · N 거절" }),
      h("button", { class: "btn quiet", onclick: () => ans({ allow: false }) }, "거절"),
      h("button", { class: "btn", onclick: () => ans({ allow: true, always: true }) }, "이 프로젝트에서 항상"),
      h("button", { class: "btn primary", onclick: () => ans({ allow: true }) }, "허용")));
}
function askCard(a) {
  const input = h("input", { class: "input", placeholder: "직접 답하기", onkeydown: (e) => { if (e.key === "Enter") send({ text: input.value }); } });
  const send = (data) => call("answer", S.current, a.id, JSON.stringify(data)).then(refresh);
  return h("div", { class: "card" },
    h("div", { class: "ch" }, h("h3", { text: "질문" })),
    h("div", { class: "desc", style: "color:var(--text);font-size:14px", text: a.question }),
    h("div", { class: "opts" }, ...(a.choices || []).map((c) => h("button", { class: "opt", onclick: () => send({ choice: c }) }, c)),
      h("div", { style: "flex:1;min-width:200px;display:flex;gap:6px" }, input, h("button", { class: "btn", onclick: () => send({ text: input.value }) }, "보내기"))));
}
function handoffCard(o) {
  const D = S.detail;
  const done = D.run.accepted;
  const passed = D.checks && D.checks.cases ? `${D.checks.passed}/${D.checks.cases} 통과` : "";
  return h("div", { class: "card" + (done ? " done" : "") },
    h("div", { class: "ch" }, h("h3", { text: "인수인계" }),
      h("span", { class: "tag", text: D.run.accepted ? "수락함" : "검토 후 수락" })),
    h("div", { class: "handoff" },
      h("h4", { text: "바뀐 것" }), h("p", { text: o.summary }),
      h("h4", { text: "실행 방법" }), h("div", { class: "cmd", text: o.how_to_run }),
      passed ? h("h4", { text: "검증" }) : null, passed ? h("p", { text: `합격 기준 ${passed} · 완료 조건 ${D.gates.filter((g) => g.ok).length}/${D.gates.length}` }) : null,
      o.risks ? h("h4", { text: "남은 위험·미검증" }) : null, o.risks ? h("p", { text: o.risks }) : null,
      o.follow_ups ? h("h4", { text: "이어서 할 일" }) : null, o.follow_ups ? h("p", { text: o.follow_ups }) : null),
    h("div", { class: "cf" }, h("span", { class: "hint", text: "고칠 점이 있으면 입력창으로 요청하세요" }),
      D.run.mode === "plan" ? h("button", { class: "btn", onclick: () => { S.tab = "brief"; S.sig.right = ""; renderRight(); } }, "기획서 보기")
        : h("button", { class: "btn", onclick: () => { S.tab = "changes"; S.sig.right = ""; renderRight(); } }, "변경 사항 보기"),
      D.run.mode === "plan" ? h("button", { class: "btn primary", onclick: () => call("readBrief", S.current).then((r) => r.ok && r.data.path && buildFromPlan(r.data.path)) }, "이 기획으로 만들기") : null,
      D.run.accepted ? null : h("button", { class: "btn primary", onclick: () => call("accept", S.current).then((r) => r.ok && (toast("수락했습니다"), refresh())) }, "수락")));
}

// ---------------------------------------------------------------- 기획서 보기 (마크다운을 안전하게 — HTML 은 글자로)
function inlineMd(text) {
  const out = []; const re = /(\*\*[^*]+\*\*|`[^`]+`)/g; let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(document.createTextNode(text.slice(last, m.index)));
    out.push(m[0].startsWith("**") ? h("b", { text: m[0].slice(2, -2) }) : h("code", { text: m[0].slice(1, -1) }));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(document.createTextNode(text.slice(last)));
  return out;
}
function renderMd(md) {
  const root = h("div", { class: "md" }); const lines = md.replace(/<!--[\s\S]*?-->/g, "").split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^```/.test(l)) { const buf = []; i++; while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]); i++; root.append(h("pre", { text: buf.join("\n") })); continue; }
    const hm = l.match(/^(#{1,6})\s+(.*)/);
    if (hm) { root.append(h("h" + Math.min(4, hm[1].length), {}, ...inlineMd(hm[2]))); i++; continue; }
    if (/^\s*\|/.test(l)) {
      const rows = []; while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
      root.append(h("table", {}, ...body.map((r, k) => h("tr", {}, ...cells(r).map((c) => h(k === 0 ? "th" : "td", {}, ...inlineMd(c)))))));
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l); const list = h(ordered ? "ol" : "ul");
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) list.append(h("li", {}, ...inlineMd(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""))));
      root.append(list); continue;
    }
    if (!l.trim()) { i++; continue; }
    const para = []; while (i < lines.length && lines[i].trim() && !/^(#|```|\s*\||\s*([-*]|\d+\.)\s)/.test(lines[i])) para.push(lines[i++]);
    root.append(h("p", {}, ...inlineMd(para.join(" "))));
  }
  return root;
}
async function briefPane(p) {
  const r = await call("readBrief", S.current);
  if (!r.ok) return;
  if (!r.data.text) { p.append(h("div", { class: "empty", text: "기획서를 쓰는 중입니다.\n질문이 오면 답해 주세요." })); return; }
  p.append(h("div", { class: "briefbar" }, h("span", { class: "mono", text: r.data.path.split(/[\\/]/).slice(-3).join("/") }),
    h("button", { class: "btn quiet sm", onclick: () => call("openFolder", r.data.path.replace(/[\\/][^\\/]+$/, "")) }, "폴더 열기"),
    h("button", { class: "btn primary sm", onclick: () => buildFromPlan(r.data.path) }, "이 기획으로 만들기")), renderMd(r.data.text));
}
// 기획서를 첨부한 '요청사항으로 만들기'로 넘긴다 — 홈에서 폴더·에이전트를 고르고 시작
function buildFromPlan(path) {
  const d = S.draft;
  S.mode = "request"; d.request = "첨부한 기획서대로 만든다. 기획서의 요구사항·합격 기준·디자인 방향을 그대로 따른다."; d.attachments = [path];
  goHome(); toast("기획서를 첨부했습니다 — 만들 폴더를 고르고 시작하세요");
}

// 스크린샷 — 작게 보여 주고 누르면 크게
function shotImg(path, title) {
  const img = h("img", { class: "shot", alt: title, title: "크게 보기" });
  call("screenshot", path).then((r) => { if (r.ok) img.src = r.data.url; });
  img.addEventListener("click", () => {
    const big = h("div", { class: "lightbox", onclick: () => big.remove() }, h("div", { class: "lbt", text: title }), h("img", { src: img.src }));
    document.body.append(big);
  });
  return img;
}

// ---------------------------------------------------------------- 오른쪽 패널
function renderRight() {
  const D = S.detail; if (!D) return;
  const sig = JSON.stringify([S.tab, D.stage, D.gates, D.requirements, D.checks, D.plan, D.items, D.run.cost_usd, D.checkpoints, S.changes, S.file, D.spec, D.handoff, D.run.round]);
  if (sig === S.sig.right) return; S.sig.right = sig;
  const right = $("#right");
  const nfiles = S.changes ? S.changes.files.length : null;
  const tab = (id, label, badge) => h("button", { class: S.tab === id ? "on" : "", onclick: () => { S.tab = id; S.sig.right = ""; if (id === "changes") loadChanges(); renderRight(); } },
    label, badge != null ? h("span", { class: "badge", text: badge }) : null);
  const pane = h("div", { class: "pane" });
  const m = D.run.mode;
  if (m === "plan" && !["brief", "changes"].includes(S.tab)) S.tab = "brief";
  if (m === "free" && S.tab !== "changes") S.tab = "changes";
  const tabs = m === "plan" ? [tab("brief", "기획서"), tab("changes", "변경", nfiles)] : m === "free" ? [tab("changes", "변경", nfiles)]
    : [tab("progress", "현황"), D.spec ? tab("spec", "기준") : null, tab("checks", "검증", D.checks && D.checks.cases ? `${D.checks.passed}/${D.checks.cases}` : null), tab("changes", "변경", nfiles)];
  right.replaceChildren(h("div", { class: "tabs" }, ...tabs.filter(Boolean)), pane);
  if (S.tab === "brief") briefPane(pane);
  else if (S.tab === "progress") progressPane(pane);
  else if (S.tab === "changes") changesPane(pane);
  else if (S.tab === "spec" && D.spec) specPane(pane);
  else checksPane(pane);
}
function progressPane(p) {
  const D = S.detail;
  const reqs = D.requirements || [];
  const impl = reqs.filter((r) => r.status === "ported").length;
  const gates = D.gates || [];
  const gok = gates.filter((g) => g.ok).length;
  const ck = D.checks || {};
  p.append(h("div", { class: "sec" }, h("h4", { text: "현황" }), h("div", { class: "stats" },
    stat(D.run.mode === "request" ? `${impl}/${reqs.length}` : D.items ? `${D.items.done}/${D.items.total}` : "—", D.run.mode === "request" ? "구현된 요구사항" : "처리한 항목",
      D.run.mode === "request" ? (reqs.length ? impl / reqs.length : 0) : D.items && D.items.total ? D.items.done / D.items.total : 0),
    stat(ck.cases ? `${ck.passed}/${ck.cases}` : "—", "케이스 통과", ck.cases ? ck.passed / ck.cases : 0),
    stat(gates.length ? `${gok}/${gates.length}` : "—", "완료 조건", gates.length ? gok / gates.length : 0),
    stat(fmtMoney(D.run.cost_usd), `사용량 · ${D.run.round || 0}라운드`))));
  if (D.plan && D.plan.steps && D.plan.steps.length) {
    const done = D.plan.steps.filter((s) => s.status === "done").length;
    p.append(h("div", { class: "sec plan" }, h("h4", {}, "계획", h("span", { text: D.plan.approved ? `${done}/${D.plan.steps.length}` : "승인 전" })),
      ...D.plan.steps.map((s, i) => h("div", { class: "p " + (s.status || "") }, h("i", { text: s.status === "done" ? "" : String(i + 1) }),
        s.status === "done" ? null : null, h("span", { text: s.title })))));
  }
  if (reqs.length) p.append(h("div", { class: "sec" }, h("h4", { text: "요구사항" }), ...reqs.map((r) => h("div", { class: "li" },
    h("span", { class: "k", text: r.id }), h("span", { class: "x" }, r.text, r.target ? h("span", { class: "f", text: r.target }) : null),
    h("span", { class: "s " + (r.status === "ported" ? "ok" : r.status === "omitted" ? "" : "wait"), text: { ported: "구현됨", omitted: "제외", pending: "남음", replaced: "대체" }[r.status] || r.status })))));
  if (gates.length) p.append(h("div", { class: "sec" }, h("h4", { text: "완료 조건" }),
    ...gates.map((g) => h("div", { class: "gate" + (g.ok ? " ok" : ""), title: g.name }, g.ok ? ico("check", "gi") : h("i"), g.label || g.name))));
}
function stat(v, l, ratio) {
  return h("div", { class: "stat" }, h("div", { class: "v", text: v }), h("div", { class: "l", text: l }),
    ratio != null ? h("div", { class: "bar2" }, h("i", { style: `width:${Math.round((ratio || 0) * 100)}%` })) : null);
}
async function loadChanges() {
  const r = await call("changes", S.current);
  if (r.ok) { S.changes = r.data; if (S.file && !S.changes.files.find((f) => f.path === S.file)) S.file = null; renderRight(); }
}
function changesPane(p) {
  const C = S.changes;
  if (!C) { p.append(h("div", { class: "empty", text: "불러오는 중…" })); if (!S.changesLoading) { S.changesLoading = true; loadChanges().finally(() => { S.changesLoading = false; }); } return; }
  if (!C.files.length) { p.append(h("div", { class: "empty", text: "시작 전과 달라진 파일이 없습니다." })); return; }
  const add = C.files.reduce((a, f) => a + (f.added || 0), 0), rem = C.files.reduce((a, f) => a + (f.removed || 0), 0);
  p.append(h("div", { class: "sec" }, h("h4", {}, `파일 ${C.files.length}개`, h("span", { text: `+${add} −${rem}` })),
    h("div", { class: "files" }, ...C.files.map((f) => h("div", { class: "fl" + (S.file === f.path ? " on" : ""), onclick: () => { S.file = S.file === f.path ? null : f.path; renderRight(); } },
      h("span", { class: "badge-st " + f.status, text: f.status }), h("span", { class: "p", text: f.path, title: f.path }),
      f.added != null ? h("span", { class: "ad", text: "+" + f.added }) : null, f.removed ? h("span", { class: "rm", text: "−" + f.removed }) : null)))));
  if (S.file) {
    p.append(h("div", { style: "display:flex;gap:6px" }, h("span", { class: "sp" }),
      h("button", { class: "btn quiet", onclick: async () => { if (!confirm(`${S.file} 를 시작 전 상태로 되돌릴까요?`)) return;
        const r = await call("revertFile", S.current, S.file); if (r.ok) { toast("되돌렸습니다"); loadChanges(); } } }, "이 파일 되돌리기")));
    p.append(diffView(C.diff, S.file));
  }
  p.append(h("div", { class: "sec", style: "margin-top:20px" }, h("h4", { text: "체크포인트" }),
    ...(S.detail.checkpoints || []).map((c) => h("div", { class: "cpl" }, h("span", { text: c.label }), h("span", { class: "t", text: ago(c.at) }),
      h("button", { class: "btn quiet", style: "padding:2px 8px;font-size:12px", onclick: () => restore(c.id, c.label) }, "되돌리기")))));
}
function diffView(diff, file) {
  const out = h("div", { class: "diff" });
  let on = false;
  for (const line of diff.split("\n")) {
    if (line.startsWith("diff --git")) { on = line.includes(" b/" + file); continue; }
    if (!on || line.startsWith("index ") || line.startsWith("---") || line.startsWith("+++") || line.startsWith("new file") || line.startsWith("deleted file")) continue;
    const cls = line.startsWith("@@") ? "h" : line.startsWith("+") ? "a" : line.startsWith("-") ? "d" : "";
    out.append(h("div", { class: cls, text: line || " " }));
  }
  return out;
}
function stamp(at) { if (!at) return ""; const d = new Date(at); return `${d.getMonth() + 1}/${d.getDate()} ${hhmm(at)}`; }
// 기준 — 무엇을 만들기로 합의했나 (요구사항 · 합격 기준 · 계획, 누가 언제 승인했나)
function specPane(p) {
  const D = S.detail, sp = D.spec, reqs = D.requirements || [];
  const ap = sp.approved;
  p.append(h("div", { class: "explain" }, h("b", { text: "합의한 기준" }),
    h("span", { text: ap ? `${ap.by || "사용자"}가 ${stamp(ap.at)}에 ${ap.channel === "desktop-app" ? "앱에서 " : ""}승인` : "아직 승인 전" })));
  p.append(h("div", { class: "sec" }, h("h4", {}, "요구사항", h("span", { text: String(reqs.length) })),
    ...reqs.map((r) => h("div", { class: "li" }, h("span", { class: "k", text: r.id }), h("span", { class: "x", text: r.text }), h("span")))));
  p.append(h("div", { class: "sec" }, h("h4", {}, "합격 기준", h("span", { text: `${sp.cases.length}개` })),
    h("div", { class: "hint2", text: "이 입력을 넣으면 이 결과가 나와야 완료로 봅니다" }),
    ...sp.cases.map((c) => h("div", { class: "case" }, h("div", { class: "ct" }, h("b", { text: c.description || c.name }), h("span", { class: "rq", text: c.reqs.join(" ") })),
      h("div", { class: "io" }, h("span", { class: "in", text: inputText(c.input) }), h("i", { text: "→" }), h("span", { text: expectText(c.expect) }))))));
  if (D.plan && D.plan.steps && D.plan.steps.length) {
    p.append(h("div", { class: "sec" }, h("h4", {}, "계획", h("span", { text: D.plan.approved ? `${D.plan.approved_by || "사용자"} 승인${D.plan.approved_at ? " · " + stamp(D.plan.approved_at) : ""}` : "승인 전" })),
      D.plan.summary ? h("div", { class: "hint2", text: D.plan.summary }) : null,
      ...D.plan.steps.map((st, i) => h("div", { class: "pstep" }, h("span", { class: "k", text: String(i + 1).padStart(2, "0") }),
        h("div", {}, h("div", { text: st.title }), (st.files || []).length || (st.commands || []).length
          ? h("div", { class: "f", text: [...(st.files || []), ...(st.commands || []).map((c) => "$ " + c)].join("   ") }) : null)))));
  }
}
// 검증 — 무엇을 어떻게 확인했나 (요구사항별 케이스: 입력 → 기대 → 실제)
function checksPane(p) {
  const D = S.detail, ck = D.checks, sp = D.spec;
  if (!ck || !ck.cases || !sp) { p.append(h("div", { class: "empty", text: "아직 검증 전입니다.\n구현이 끝나면 승인된 합격 기준을 지금 코드에 실제로 실행해 확인합니다." })); return; }
  const fails = sp.cases.filter((c) => c.ok === false).length;
  p.append(h("div", { class: "explain" + (fails ? " bad" : " good") }, h("b", { text: fails ? `${fails}개가 기준과 다릅니다` : `합격 기준 ${ck.passed}/${ck.cases} 모두 통과` }),
    h("span", { text: `승인된 합격 기준을 지금 코드에 실제로 실행해 기대 결과와 비교했습니다${sp.verified_at ? " · " + stamp(sp.verified_at) : ""}.` })));
  const reqs = D.requirements || [];
  const groups = reqs.map((r) => ({ r, cases: sp.cases.filter((c) => c.reqs.includes(r.id)) }));
  const loose = sp.cases.filter((c) => !c.reqs.length);
  if (loose.length) groups.push({ r: { id: "—", text: "요구사항 연결 없음" }, cases: loose });
  for (const g of groups) {
    const ok = g.cases.length && g.cases.every((c) => c.ok);
    p.append(h("div", { class: "rgroup" }, h("div", { class: "rh" }, ok ? ico("check", "gi") : h("span", { class: "ci fail", text: g.cases.length ? "!" : "?" }),
      h("span", { class: "k", text: g.r.id }), h("span", { text: g.r.text })),
      ...g.cases.map((c) => h("div", { class: "case" + (c.ok === false ? " bad" : "") }, h("div", { class: "ct" }, h("b", { text: c.description || c.name }),
          h("span", { class: "res " + (c.ok ? "ok" : c.ok === false ? "fail" : ""), text: c.ok ? "통과" : c.ok === false ? "다름" : "미실행" })),
        h("div", { class: "io" }, h("span", { class: "in", text: inputText(c.input) }), h("i", { text: "→" }), h("span", { text: "기대 " + expectText(c.expect) })),
        c.screenshot ? shotImg(c.screenshot, c.description || c.name)
          : c.actual ? h("div", { class: "io act" }, h("span", { text: "실제 " + [c.actual.status, c.actual.body, c.actual.error].filter((x) => x != null).join(" · ") })) : null,
        c.diffs.length ? h("div", { class: "io bad", text: c.diffs.join("  ") }) : null))));
  }
  const help = sp.gates_help || {};
  p.append(h("div", { class: "sec", style: "margin-top:18px" }, h("h4", { text: "함께 확인한 완료 조건" }),
    ...(D.gates || []).map((g) => h("div", { class: "gate2" + (g.ok ? " ok" : "") }, g.ok ? ico("check", "gi") : h("i"),
      h("div", {}, h("div", { text: g.label || g.name }), help[g.name] ? h("div", { class: "f", text: help[g.name] }) : null)))));
}

// ---------------------------------------------------------------- 갱신·단축키
// 화면에 보이는 내용의 지문을 함께 보낸다 — 그 사이 에이전트가 바꿨으면 승인되지 않고 새 내용을 다시 보여 준다
async function approveShown(what) {
  // 처음에 한 번 확인: 기준과 계획이 함께 떠 있으면 한 번에 승인한다 (각각 보인 내용 그대로)
  const D = S.detail;
  const reviews = D.reviews && D.reviews.count, plan = D.plan && D.plan.steps && D.plan.steps.length && !D.plan.approved;
  let ok = true;
  if (reviews && (what === "reviews" || what === "both")) ok = (await call("approveReviews", S.current, D.review_sig || "")).ok && ok;
  if (plan && ok && (what === "plan" || what === "both")) ok = (await call("approvePlan", S.current, D.plan_sig || "")).ok && ok;
  if (ok) toast(reviews && plan && what === "both" ? "기준과 계획을 승인했습니다 — 끝날 때까지 진행합니다" : "승인했습니다");
  refresh();
}
function bothPending() { const D = S.detail; return !!(D && D.reviews && D.reviews.count && D.plan && D.plan.steps && D.plan.steps.length && !D.plan.approved); }
async function refresh() {
  const lr = await call("listRuns"); if (lr.ok) S.runs = lr.data;
  renderSide();
  if (S.current) {
    const want = S.current;
    const r = await call("runDetail", want);
    if (r.ok && want === S.current && r.data.run.id === S.current) { S.detail = r.data; renderRun(); }  // 늦게 온 다른 실행의 응답은 버린다
  } else if (!$("#homeText")) renderHome();
}
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey && (e.key === "n" || e.key === "N")) { e.preventDefault(); goHome(); }
  if (e.key === "Escape" && openPop) { closePop(); return; }
  if (e.ctrlKey && e.key === "Enter" && !S.current && !S.preparing) { e.preventDefault(); create(); return; }
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || !S.detail) return;  // 입력 중의 Ctrl+Enter 는 보내기 — 승인이 아님, Esc 도 멈춤이 아님
  if (e.key === "Escape" && S.detail.running) { call("stop", S.current).then(refresh); return; }
  if (e.ctrlKey && e.key === "Enter") {
    if (S.edit) return;
    if (bothPending()) approveShown("both");
    else if (S.detail.reviews && S.detail.reviews.count) approveShown("reviews");
    else if (S.detail.plan && S.detail.plan.steps && !S.detail.plan.approved) approveShown("plan");
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;  // Ctrl+A(전체 선택) 등이 권한 답이 되지 않게
  const perm = (S.detail.asks || []).find((a) => a.kind === "permission");
  if (perm && ["y", "n"].includes(e.key.toLowerCase())) {  // '항상 허용'은 단축키로 주지 않는다 — 버튼으로만
    const data = { y: { allow: true }, n: { allow: false } }[e.key.toLowerCase()];
    call("answer", S.current, perm.id, JSON.stringify(data)).then(refresh);
  }
});
function goHome() { closePop(); S.edit = null; S.current = null; S.detail = null; S.sig = { feed: "", right: "", side: "", cbar: "" }; S.preparing = false; renderSide(); renderHome(); }
$("#newbtn").addEventListener("click", goHome);

new QWebChannel(qt.webChannelTransport, async (ch) => {
  api = ch.objects.api;
  api.created.connect((raw) => {
    const r = JSON.parse(raw); S.preparing = false;
    if (r.error) { toast(r.error); renderHome(); return; }
    S.draft.request = ""; openRun(r.run_id).then(() => r.start && startRun());
  });
  const b = await call("bootstrap");
  if (b.ok) { S.agents = b.data.agents; S.runs = b.data.runs; S.kinds = b.data.kinds; S.policy = b.data.default_policy; S.permModes = b.data.permission_modes || []; $("#ver").textContent = "v" + b.data.version;
    const def = b.data.defaults || {};  // 지난번 새 작업의 선택
    for (const k of ["agent", "model", "effort", "max_rounds", "plan_approval", "confirm_at", "auth"]) if (def[k] != null && def[k] !== "") S.draft[k] = def[k];
    if (def.policy) S.draft.policy = { ...def.policy };
    pickReadyAgent(); }
  const sc = await call("shortcutStatus"); if (sc.ok) S.shortcut = sc.data;
  renderSide(); renderHome();
  api.dataReady.connect((raw) => { const r = JSON.parse(raw); if (r.ok && openPop && openPop.querySelector(".datapop")) { S.data = r.data; drawData($("#databtn")); } });
  $("#databtn").addEventListener("click", (e) => { e.stopPropagation(); if (openPop && openPop.querySelector(".datapop")) { closePop(); return; } dataPop(e.currentTarget); });
  api.agentsChanged.connect((raw) => { S.agents = JSON.parse(raw); pickReadyAgent(); renderSide(); if (!S.current) renderHome(); });
  // 진행 중일 때만 자주(1.5초), 한가하면 5초, 창이 가려져 있으면 쉰다 — 앱이 쓸데없이 일하지 않게
  const tick = async () => {
    if (!document.hidden) { try { await refresh(); } catch (e) { /* 다음 번에 다시 */ } }
    const busy = (S.detail && S.detail.running) || S.runs.some((r) => r.tone === "run");
    setTimeout(tick, document.hidden ? 4000 : busy ? 1500 : 5000);
  };
  setTimeout(tick, 1500);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setInterval(() => { if (!document.hidden) call("agentStatus").then((r) => { if (r.ok) { S.agents = r.data; renderSide(); } }); }, 60000);
});
