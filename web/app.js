// Today window: page logic. Loaded by today_gui.py, which inlines it into web/index.html (no server).
"use strict";
const $ = (id) => document.getElementById(id);
const S = { date: null, st: null, view: "plan", filter: "all", editing: null, dailyEdit: null, lastV: "", busy: false, lastRender: 0 };

/* ---------- tiny DOM helper (text is always set as text, never parsed as HTML) ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}

/* two-click delete: first click arms the button, second confirms */
function confirmBtn(label, cls, onConfirm) {
  const b = h("button", { class: cls }, label);
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    if (b.dataset.armed) { onConfirm(); return; }
    b.dataset.armed = "1"; b.textContent = "Sure?";
    setTimeout(() => { delete b.dataset.armed; b.textContent = label; }, 2500);
  });
  return b;
}

/* ---------- bridge ---------- */
async function call(name, ...args) {
  try {
    const r = await window.pywebview.api[name](...args);
    if (r && r.error) { toast(r.error, true); return null; }
    return r;
  } catch (e) { toast(String(e), true); return null; }
}
let toastT;
function toast(msg, err) {
  const t = $("toast"); t.textContent = msg; t.className = "toast show" + (err ? " error" : "");
  clearTimeout(toastT); toastT = setTimeout(() => (t.className = "toast" + (err ? " error" : "")), err ? 4200 : 2200);
}

/* ---------- dates ---------- */
function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
function addDays(s, n) { const [y, m, d] = s.split("-").map(Number); return iso(new Date(y, m - 1, d + n)); }
function nowHHMM() { const d = new Date(); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); }
function fmtNow() { const d = new Date(); let h = d.getHours() % 12 || 12; return h + ":" + String(d.getMinutes()).padStart(2, "0") + (d.getHours() < 12 ? " AM" : " PM"); }

/* ---------- load + render ---------- */
async function refresh(force) {
  if (S.busy) { if (force) S.pending = true; return; }
  if (!force && (S.editing || S.dailyEdit)) return;   // don't yank a form out from under the cursor
  if (!force && document.activeElement && document.activeElement.closest(".detail")) return;
  S.busy = true;
  const st = await call("state", S.date, $("showHidden").checked);
  S.busy = false;
  if (S.pending) { S.pending = false; return refresh(true); }
  if (!st) return;
  S.st = st; S.date = st.date; S.lastV = st.version; S.lastRender = Date.now();
  render();
  if (S.view === "apps") loadApps();
  if (S.view === "jobs") loadJobs();
}

function render() {
  const st = S.st; if (!st) return;
  $("boot").style.display = "none";
  const [y, m, d] = st.today.split("-").map(Number);
  const td = new Date(y, m - 1, d);
  $("sdDow").textContent = td.toLocaleDateString("en-US", { weekday: "long" });
  $("sdDay").textContent = td.toLocaleDateString("en-US", { month: "long", day: "numeric" });
  renderPlan(); renderDaily(); renderDeadlines(); renderInbox(); renderBadges(); renderScanInfo();
}

function renderBadges() {
  const st = S.st;
  const open = st.is_today ? st.items.filter((i) => !i.done).length : null;
  $("bPlan").textContent = open ? open : "";
  const c = st.counts, hot = c.offer + c.interview + c.oa_open;
  const b = $("bInbox"); b.textContent = hot || ""; b.className = "badge" + (hot ? " hot" : "");
  $("bMail").textContent = st.accounts.length ? st.accounts.length : "";
  const at = st.apps_totals || {};
  $("bApps").textContent = at.applications ? at.applications : "";
  $("bJobs").textContent = st.jobs_unreported ? st.jobs_unreported : "";   // an unfinished batch is waiting
}

function renderScanInfo() {
  const st = S.st, el = $("scanInfo"), errs = Object.keys(st.inbox_errors || {});
  if (st.scanning) return;
  el.className = "scaninfo" + (errs.length ? " err" : "");
  if (!st.accounts.length) el.textContent = "No mail accounts yet";
  else if (errs.length) el.textContent = "Scan problem: " + errs.join(", ");
  else el.textContent = st.inbox_updated ? "Checked " + st.inbox_updated.replace(/^\d{4}-/, "") : "Not checked yet";
}

/* ---------- plan ---------- */
function renderPlan() {
  const st = S.st;
  $("pTitle").textContent = st.human;
  const rel = $("pRel"); rel.textContent = st.rel || (st.is_past ? "Past" : "Upcoming");
  rel.className = "pill" + (st.is_past ? " past" : "");
  $("dToday").disabled = st.is_today;
  const done = st.items.filter((i) => i.done).length;
  $("pProg").textContent = st.items.length ? done + " of " + st.items.length + " done" : "";
  const mins = st.items.filter((i) => !i.done && i.mins).reduce((a, i) => a + i.mins, 0);
  $("sSub").textContent = mins ? fmtMins(mins) + " left" : "";

  const carry = $("carry"); carry.replaceChildren();
  if (st.carry.n) {
    carry.append(h("div", { class: "banner" },
      h("span", {}, st.carry.n + " unfinished from " + st.carry.from_fmt),
      h("button", { class: "btn primary", onclick: async () => { const r = await call("plan_carry", S.date); if (r) { toast("Carried " + r.n + " over"); refresh(true); } } }, "Carry over")));
  }

  const box = $("sched"); box.replaceChildren();
  if (!st.items.length) { box.append(h("div", { class: "empty" }, st.is_past ? "Nothing was planned." : "Nothing planned yet. Type above and press Return.")); return; }
  const timed = st.items.filter((i) => i.time), loose = st.items.filter((i) => !i.time);
  const now = nowHHMM();
  let nowDrawn = !st.is_today;
  if (timed.length) {
    box.append(h("div", { class: "sect" }, "Scheduled"));
    for (const it of timed) {
      if (!nowDrawn && it.time > now) { box.append(h("div", { class: "nowline" }, fmtNow())); nowDrawn = true; }
      box.append(planRow(it)); if (S.editing === it.id) box.append(editForm(it));
    }
    if (!nowDrawn) box.append(h("div", { class: "nowline" }, fmtNow()));
  }
  if (loose.length) {
    box.append(h("div", { class: "sect" }, "Anytime"));
    for (const it of loose) { box.append(planRow(it)); if (S.editing === it.id) box.append(editForm(it)); }
  }
}

function fmtMins(n) { return n >= 60 ? Math.floor(n / 60) + "h" + (n % 60 ? " " + String(n % 60).padStart(2, "0") + "m" : "") : n + "m"; }

function planRow(it) {
  const st = S.st;
  const meta = [];
  if (it.mins_fmt && !it.time) meta.push(h("span", { class: "chip" }, it.mins_fmt));
  if (it.note) meta.push(h("span", { class: "selectable" }, it.note));
  const acts = h("div", { class: "acts" },
    it.time ? h("button", { class: "ibtn", title: "15 min earlier", onclick: (e) => { e.stopPropagation(); shift(it, -15); } }, "−15") : null,
    it.time ? h("button", { class: "ibtn", title: "15 min later", onclick: (e) => { e.stopPropagation(); shift(it, 15); } }, "+15") : null,
    !it.done ? h("button", { class: "ibtn", title: st.is_past ? "Move to today" : "Move to the next day",
      onclick: async (e) => { e.stopPropagation(); const to = st.is_past ? st.today : addDays(S.date, 1); if (await call("plan_move", S.date, it.id, to)) { toast("Moved to " + (to === st.today ? "today" : to === addDays(st.today, 1) ? "tomorrow" : to)); refresh(true); } } }, st.is_past ? "→ today" : "→ next day") : null,
    h("button", { class: "ibtn", title: "Edit", onclick: (e) => { e.stopPropagation(); openEdit(it.id); } }, "Edit"),
    confirmBtn("Delete", "ibtn del", async () => { if (await call("plan_remove", S.date, it.id)) refresh(true); }));
  return h("div", { class: "row" + (it.done ? " done" : "") + (it.urgent ? " urgent" : ""), ondblclick: () => openEdit(it.id) },
    h("button", { class: "check" + (it.done ? " on" : ""), title: it.done ? "Mark not done" : "Mark done",
      onclick: async () => { if (await call("plan_update", S.date, it.id, { done: !it.done })) refresh(true); } }),
    it.time ? h("div", { class: "time" }, h("b", {}, it.time_fmt), it.end_fmt ? h("i", {}, "to " + it.end_fmt) : null) : null,
    h("div", { class: "main-col" }, h("div", { class: "title selectable" }, it.title), meta.length ? h("div", { class: "meta" }, meta) : null),
    it.urgent && !it.done ? h("span", { class: "dot", title: "Urgent" }) : null,
    acts);
}

async function shift(it, d) { if (await call("plan_shift", S.date, it.id, d)) refresh(true); }
function openEdit(id) { S.editing = S.editing === id ? null : id; renderPlan(); const f = document.querySelector(".edit input"); if (f) f.focus(); }

function editForm(it) {
  const title = h("input", { class: "field", value: it.title });
  const time = h("input", { class: "field", value: it.time_fmt, placeholder: "6:10pm" });
  const mins = h("input", { class: "field", value: it.mins || "", placeholder: "45" });
  const note = h("textarea", { class: "field", placeholder: "Note" }); note.value = it.note;
  const urg = h("input", { type: "checkbox" }); urg.checked = it.urgent;
  const save = async () => {
    const r = await call("plan_update", S.date, it.id, { title: title.value, time: time.value, mins: mins.value, note: note.value, urgent: urg.checked });
    if (r) { S.editing = null; refresh(true); }
  };
  const form = h("div", { class: "edit", onkeydown: (e) => {
      if (e.key === "Enter" && (e.target.tagName !== "TEXTAREA" || e.metaKey)) { e.preventDefault(); save(); }
      if (e.key === "Escape") { S.editing = null; renderPlan(); }
    } },
    h("label", {}, "Title", title), h("label", {}, "Time", time), h("label", {}, "Minutes", mins),
    h("label", { class: "full" }, "Note", note),
    h("div", { class: "bar" }, h("label", { class: "toggle" }, urg, "Urgent"), h("div", { class: "spacer" }),
      h("button", { class: "btn", onclick: () => { S.editing = null; renderPlan(); } }, "Cancel"),
      h("button", { class: "btn primary", onclick: save }, "Save")));
  return form;
}

/* ---------- daily ---------- */
function renderDaily() {
  const st = S.st, box = $("daily"); box.replaceChildren();
  const done = st.daily.filter((d) => d.done).length;
  $("dSub").textContent = st.daily.length ? done + "/" + st.daily.length : "";
  if (!st.daily.length) { box.append(h("div", { class: "empty" }, "No daily tasks yet.")); return; }
  for (const d of st.daily) {
    if (S.dailyEdit === d.id) { box.append(dailyEditRow(d)); continue; }
    const acts = h("div", { class: "acts" },
      h("button", { class: "ibtn", onclick: () => { S.dailyEdit = d.id; renderDaily(); const f = document.querySelector("#daily input"); if (f) f.focus(); } }, "Edit"),
      confirmBtn("Delete", "ibtn del", async () => { if (await call("daily_remove", d.id)) refresh(true); }));
    if (d.target) {
      const pct = Math.min(100, Math.round((d.count / d.target) * 100));
      box.append(h("div", { class: "row" + (d.done ? " done-c" : ""), ondblclick: () => { S.dailyEdit = d.id; renderDaily(); } },
        h("button", { class: "check" + (d.done ? " on" : ""), title: d.done ? "Reset to 0" : "Mark all done", onclick: () => dailyToggle(d) }),
        h("div", { class: "main-col" }, h("div", { class: "title" }, d.title),
          h("div", { class: "counter" }, h("div", { class: "bar-bg" }, h("div", { class: "bar-fg", style: "width:" + pct + "%" })),
            h("span", { class: "num" }, d.count + "/" + d.target))),
        acts,
        h("div", { class: "step" },
          h("button", { title: "−1", onclick: () => dailyInc(d, -1) }, "−"),
          h("button", { title: "+1 (⇧ for +5)", onclick: (e) => dailyInc(d, e.shiftKey ? 5 : 1) }, "+"))));
    } else {
      box.append(h("div", { class: "row" + (d.done ? " done" : ""), ondblclick: () => { S.dailyEdit = d.id; renderDaily(); } },
        h("button", { class: "check" + (d.done ? " on" : ""), onclick: () => dailyToggle(d) }),
        h("div", { class: "main-col" }, h("div", { class: "title" }, d.title)), acts));
    }
  }
}
async function dailyToggle(d) { if (await call("daily_toggle", d.id, S.date)) refresh(true); }
async function dailyInc(d, k) { if (await call("daily_inc", d.id, S.date, k)) refresh(true); }
function dailyEditRow(d) {
  const t = h("input", { class: "field", value: d.title, style: "flex:1;min-width:0" });
  const n = h("input", { class: "field", value: d.target || "", placeholder: "count", style: "width:64px", title: "Target count (empty = checkbox)" });
  const save = async () => { if (await call("daily_edit", d.id, t.value, n.value)) { S.dailyEdit = null; refresh(true); } };
  return h("div", { class: "row", style: "gap:6px", onkeydown: (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") { S.dailyEdit = null; renderDaily(); } } },
    t, n, h("button", { class: "btn primary", onclick: save }, "Save"));
}

/* ---------- deadlines ---------- */
function renderDeadlines() {
  const st = S.st, box = $("deadlines"); box.replaceChildren();
  const open = st.deadlines.filter((d) => !d.done).length;
  $("lSub").textContent = open ? open + " open" : "";
  if (!st.deadlines.length) { box.append(h("div", { class: "empty" }, "No deadlines.")); return; }
  for (const d of st.deadlines) {
    box.append(h("div", { class: "row" + (d.done ? " done" : "") },
      h("button", { class: "check" + (d.done ? " on" : ""), title: d.done ? "Mark open" : "Mark done",
        onclick: async () => { if (await call("deadline_act", d.id, d.source, d.done ? "undo" : "done")) refresh(true); } }),
      h("div", { class: "main-col" },
        h("div", { class: "title selectable" }, d.name),
        h("div", { class: "meta" }, d.when_fmt, d.source === "inbox" ? h("span", { class: "from" }, "  ·  from mail") : null,
          d.note ? h("div", { class: "selectable" }, d.note) : null)),
      h("div", { class: "acts" }, h("button", { class: "ibtn del", title: d.source === "inbox" ? "Hide" : "Delete",
        onclick: async () => { if (await call("deadline_act", d.id, d.source, "remove")) refresh(true); } }, d.source === "inbox" ? "Hide" : "Delete")),
      h("span", { class: "until sev-" + d.sev }, d.until)));
  }
}

/* ---------- inbox ---------- */
const FILTERS = [["all", "All"], ["oa", "Assessments"], ["interview", "Interviews"], ["event", "Events"], ["offer", "Offers"], ["important", "Flagged"], ["rejection", "Rejections"]];
function renderInbox() {
  const st = S.st;
  const errs = Object.entries(st.inbox_errors || {});
  const n = $("iNotice");
  n.className = "notice" + (errs.length ? " err" : "");
  n.textContent = errs.length ? errs.map(([a, e]) => a + ": " + e).join("  ·  ")
    : st.summary + (st.inbox_updated ? "  ·  checked " + st.inbox_updated : "") + (st.accounts.length ? "" : "  ·  add a mail account to scan automatically");
  const counts = {}; for (const i of st.inbox) counts[i.kind] = (counts[i.kind] || 0) + 1;
  const chips = $("iChips"); chips.replaceChildren();
  for (const [k, label] of FILTERS) {
    if (k !== "all" && !counts[k]) continue;
    chips.append(h("button", { class: S.filter === k ? "on" : "", onclick: () => { S.filter = k; renderInbox(); } }, label, h("span", { class: "n" }, k === "all" ? st.inbox.length : counts[k])));
  }
  const box = $("inbox"); box.replaceChildren();
  const rows = st.inbox.filter((i) => S.filter === "all" || i.kind === S.filter);
  if (!rows.length) { box.append(h("div", { class: "empty" }, "Nothing here.")); return; }
  for (const i of rows) {
    const kcls = i.kind === "oa" && i.status === "done" ? "oadone" : i.kind;
    const right = i.due_fmt ? h("div", { class: "right" }, h("div", {}, i.due_fmt), h("span", { class: "until sev-" + i.sev }, i.until))
                            : h("div", { class: "right" }, i.date_fmt);
    const acts = h("div", { class: "acts" },
      i.kind === "oa" ? h("button", { class: "ibtn", onclick: async (e) => { e.stopPropagation(); if (await call("inbox_act", i.id, "toggle_done")) refresh(true); } }, i.status === "done" ? "Reopen" : "Mark done") : null,
      h("button", { class: "ibtn del", onclick: async (e) => { e.stopPropagation(); if (await call("inbox_act", i.id, i.status === "hidden" ? "unhide" : "hide")) refresh(true); } }, i.status === "hidden" ? "Unhide" : "Hide"));
    box.append(h("div", { class: "row" + (i.status === "done" ? " done" : "") + (i.status === "hidden" ? " hidden" : ""),
        title: i.link ? "Open in Gmail" : "", onclick: () => { if (i.link) call("open_url", i.link); } },
      h("span", { class: "lbl k-" + kcls }, i.label),
      h("div", { class: "main-col" },
        h("div", {}, h("span", { class: "company" }, i.company || "?"), "  ", h("span", { class: "title" }, i.title)),
        i.detail ? h("div", { class: "meta" }, i.detail) : null),
      acts, right));
  }
}

/* ---------- applications ---------- */
const STAGE_ORDER = { applied: 0, oa: 1, oa_done: 2, interview: 3, offer: 4 };
const A_FILTERS = [
  ["all", "All", () => true],
  ["progress", "In progress", (a) => a.status === "active" && a.stage !== "applied"],
  ["waiting", "Waiting", (a) => a.status === "active" && a.stage === "applied"],
  ["oa", "Assessment", (a) => a.status === "active" && (a.stage === "oa" || a.stage === "oa_done")],
  ["interview", "Interview", (a) => a.status === "active" && a.stage === "interview"],
  ["offer", "Offer", (a) => a.stage === "offer" && a.status !== "rejected"],
  ["rejected", "Rejected", (a) => a.status === "rejected"],
  ["closed", "Withdrawn / closed", (a) => a.status === "withdrawn" || a.status === "closed"],
  ["check", "Check match", (a) => a.uncertain],
];
S.aFilter = "all"; S.aOpen = null;

async function loadApps() {
  const r = await call("apps");
  if (!r) return;
  S.apps = r;
  renderApps();
}
async function loadHistorySince() { const r = await call("apps"); if (r) $("hSince").value = r.history_since; }

function renderApps() {
  const r = S.apps; if (!r) return;
  const t = r.totals;
  // notice
  const hist = Object.keys(r.history || {});
  $("aNotice").textContent = !r.accounts.length
    ? "Showing what the inbox already had. Add a mail account (Mail accounts) and Check mail to load everything since " + r.history_since_fmt + "."
    : !hist.length ? "Check mail once to load everything since " + r.history_since_fmt + " (takes a few minutes the first time)."
    : "From your mail since " + r.history_since_fmt + (r.updated ? "  ·  updated " + r.updated.replace(/^\d{4}-/, "") : "");
  // tiles
  const pct = t.applications ? Math.round((t.heard_back / t.applications) * 100) : 0;
  const tiles = [
    ["all", "Applications", t.applications, "", "heard back from " + pct + "%"],
    ["progress", "In progress", t.in_progress, "blue", t.waiting + " waiting to hear back"],
    ["oa", "Reached assessment", t.reached_oa, "orange", ""],
    ["interview", "Reached interview", t.reached_interview, "blue", ""],
    ["offer", "Offers", t.offers, "green", ""],
    ["rejected", "Rejected", t.rejected, "red", t.rejection_emails + " rejection email" + (t.rejection_emails === 1 ? "" : "s")],
  ];
  const tb = $("aTiles"); tb.replaceChildren();
  for (const [f, l, n, cls, sub] of tiles)
    tb.append(h("div", { class: "tile " + cls, title: "Show these", onclick: () => { S.aFilter = f; renderApps(); } },
      h("div", { class: "n" }, n), h("div", { class: "l" }, l), sub ? h("div", { class: "s" }, sub) : null));
  // chart
  const ch = $("aChart"); ch.replaceChildren();
  const max = Math.max(1, ...r.weekly.map((w) => Math.max(w.applied, w.rejected)));
  r.weekly.forEach((w, i) => {
    const [y, m, d] = w.week.split("-").map(Number);
    const lab = new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric" });
    ch.append(h("div", { class: "wk", title: "Week of " + lab + ": " + w.applied + " applied, " + w.rejected + " rejected" },
      h("div", { class: "bars" },
        h("div", { class: "b a", style: "height:" + Math.round((w.applied / max) * 100) + "%" }),
        h("div", { class: "b r", style: "height:" + Math.round((w.rejected / max) * 100) + "%" })),
      h("div", { class: "lab" }, i % 2 === r.weekly.length % 2 ? "" : lab)));
  });
  // chips
  const chips = $("aChips"); chips.replaceChildren();
  for (const [k, label, fn] of A_FILTERS) {
    const n = r.apps.filter(fn).length;
    if (k === "check" && !n) continue;
    chips.append(h("button", { class: S.aFilter === k ? "on" : "", onclick: () => { S.aFilter = k; renderApps(); } }, label, h("span", { class: "n" }, n)));
  }
  // list
  const q = $("aSearch").value.trim().toLowerCase();
  const fn = (A_FILTERS.find((f) => f[0] === S.aFilter) || A_FILTERS[0])[2];
  let rows = r.apps.filter(fn).filter((a) => !q || (a.company + " " + a.role).toLowerCase().includes(q));
  const sort = $("aSort").value;
  rows.sort(sort === "company" ? (a, b) => a.company.localeCompare(b.company) || b.applied.localeCompare(a.applied)
    : sort === "applied" ? (a, b) => b.applied.localeCompare(a.applied)
    : sort === "stage" ? (a, b) => (STAGE_ORDER[b.stage] - STAGE_ORDER[a.stage]) || (a.status === "active" ? -1 : 1) - (b.status === "active" ? -1 : 1) || b.last.localeCompare(a.last)
    : (a, b) => b.last.localeCompare(a.last));
  const box = $("aList"); box.replaceChildren();
  if (!rows.length) { box.append(h("div", { class: "empty" }, r.apps.length ? "No applications match." : "No applications yet.")); return; }
  const shown = rows.slice(0, S.aLimit || 300);
  for (const a of shown) { box.append(appRow(a)); if (S.aOpen === a.id) box.append(appDetail(a)); }
  if (rows.length > shown.length)
    box.append(h("div", { class: "empty" }, h("button", { class: "linkbtn", onclick: () => { S.aLimit = rows.length; renderApps(); } }, "Show all " + rows.length)));
}

function stagePill(a) {
  if (a.status !== "active") return h("span", { class: "stage st-" + a.status }, a.status_label + (a.status_manual ? " ✎" : ""));
  return h("span", { class: "stage st-" + a.stage }, a.stage_label + (a.stage_manual ? " ✎" : ""));
}

function appRow(a) {
  const ended = a.status !== "active";
  let when1 = "Applied " + a.applied_fmt, when2 = "";
  if (a.status === "rejected") when2 = "Rejected " + a.ended_fmt;
  else if (ended) when2 = "Last update " + a.last_fmt;
  else if (a.stage !== "applied") when2 = a.stage_label + " " + a.stage_date_fmt;
  else if (a.quiet_days != null) when2 = a.quiet_days === 0 ? "today" : a.quiet_days + "d, no reply yet";
  return h("div", { class: "row" + (ended ? " ended" : "") + (S.aOpen === a.id ? " open" : ""),
      onclick: () => { S.aOpen = S.aOpen === a.id ? null : a.id; renderApps(); } },
    h("div", { class: "main-col" }, h("div", { class: "co selectable" }, a.company),
      h("div", { class: "role selectable" + (a.role ? "" : " none") }, a.role || "role not named in the email")),
    a.uncertain ? h("span", { class: "flag", title: "An email here was matched by guess. Open to check it." }, "check") : null,
    !a.confirmed ? h("span", { class: "flag", style: "color:var(--text2);background:var(--gray-soft)", title: "No confirmation email found; created from a later email" }, "no confirmation") : null,
    stagePill(a),
    h("div", { class: "when" }, h("div", {}, when1), when2 ? h("div", { class: "sub" }, when2) : null));
}

function appDetail(a) {
  const r = S.apps;
  const same = r.apps.filter((x) => x.id !== a.id && x.ckey === a.ckey);
  const tl = h("div", { class: "tl" });
  for (const e of a.events) {
    const mv = h("select", { class: "field", title: "This email belongs to…" },
      h("option", { value: "" }, e.uncertain ? "Matched by guess: move to…" : "Move to…"),
      ...same.map((x) => h("option", { value: x.id }, x.company + " · " + (x.role || "no role") + " (applied " + x.applied_fmt + ")")),
      h("option", { value: "new" }, "A separate application"));
    mv.onchange = async () => { if (mv.value && await call("app_move_event", e.key, mv.value)) { toast("Moved"); loadApps(); refresh(true); } };
    tl.append(h("div", { class: "ev" },
      h("span", { class: "d" }, e.date_fmt),
      h("span", {}, h("span", { class: "stage st-" + (e.kind === "rejection" ? "rejected" : e.kind) }, e.label)),
      h("span", { class: "s" + (e.link ? " link" : ""), title: e.link ? "Open in Gmail" : "", onclick: () => { if (e.link) call("open_url", e.link); } },
        e.source === "reported" ? "You reported applying" : e.subject + (e.source === "seed" ? "  (from the first inbox)" : "")),
      (same.length || a.events.length > 1 || e.uncertain) ? mv : h("span")));
  }
  const co = h("input", { class: "field", value: a.company });
  const role = h("input", { class: "field", value: a.role, placeholder: "Role" });
  const stage = h("select", { class: "field" }, h("option", { value: "" }, "Auto: " + ({ applied: "Applied", oa: "Assessment", oa_done: "Assessment done", interview: "Interview", offer: "Offer" })[a.auto_stage]),
    ...r.stages.map(([k, l]) => h("option", { value: k }, l)));
  stage.value = a.stage_manual ? a.stage : "";
  const status = h("select", { class: "field" }, h("option", { value: "" }, "Auto: " + (a.auto_status === "rejected" ? "Rejected" : "Active")),
    ...r.statuses.map(([k, l]) => h("option", { value: k }, l)));
  status.value = a.status_manual ? a.status : "";
  const note = h("input", { class: "field", value: a.note, placeholder: "Note (recruiter name, referral, anything)" });
  const save = async () => {
    if (await call("app_update", a.id, { company: co.value, role: role.value, stage: stage.value, status: status.value, note: note.value })) { toast("Saved"); loadApps(); refresh(true); }
  };
  let merge = null;
  if (same.length) {
    const sel = h("select", { class: "field" }, h("option", { value: "" }, "Merge into…"),
      ...same.map((x) => h("option", { value: x.id }, (x.role || "no role") + " (applied " + x.applied_fmt + ")")));
    sel.onchange = async () => { if (sel.value && await call("app_merge", a.id, sel.value)) { S.aOpen = sel.value; toast("Merged"); loadApps(); refresh(true); } };
    merge = sel;
  }
  return h("div", { class: "detail", onclick: (e) => e.stopPropagation(), onkeydown: (e) => { if (e.key === "Enter" && e.target.tagName === "INPUT") save(); if (e.key === "Escape") { S.aOpen = null; renderApps(); } } },
    tl,
    h("div", { class: "editgrid" },
      h("label", {}, "Company", co), h("label", {}, "Role", role), h("label", {}, "Stage", stage), h("label", {}, "Status", status),
      h("label", { class: "wide" }, "Note", note)),
    h("div", { class: "editbar" }, merge, h("div", { class: "spacer" }),
      confirmBtn("Delete", "btn danger", async () => { if (await call("app_delete", a.id)) { S.aOpen = null; toast("Deleted (it won't come back on rescans)"); loadApps(); refresh(true); } }),
      h("button", { class: "btn primary", onclick: save }, "Save")));
}

/* ---------- jobs ---------- */
// Presentation only. What is eligible, the order, what carries over and what counts are decided in jobs/;
// this tab shows the stored batch and sends back "applied through N", skips and one-row verify requests.
S.jobs = null; S.jOpen = null; S.jBusy = ""; S.jReport = "";

async function loadJobs() {
  const r = await call("jobs_state");
  if (!r) return;
  S.jobs = r;
  if (r.running && !S.jBusy) S.jBusy = "Working…";
  if (!r.running) S.jBusy = "";
  renderJobs();
}

function plural(n, one, many) { return n + " " + (n === 1 ? one : many || one + "s"); }

function jobFacts(j) {
  const out = [];
  if (S.jBusy) out.push(["busy", S.jBusy]);
  const open = j.unreported.length, lp = j.last_prepare;
  if (!j.rows.length) {
    out.push(["", !j.ready ? "No batch yet. Prepare fetches the feed and lists what is new since " + j.cutoff_date + "."
      : lp && lp.found === 0 ? "Nothing new and eligible since the last batch."
      : "No open batch. Prepare lists what is new since the last one."]);
  } else if (open) {
    out.push(["", plural(open, "row") + " not yet reported. Report how far you got, or skip what you passed on; the rest carries over to the top of the next batch."]);
  }
  if (lp && lp.created && lp.found < lp.requested)
    out.push(["", "Asked for " + lp.requested + ", found " + plural(lp.found, "new eligible posting") + (lp.carried ? " (plus " + lp.carried + " carried over)" : "") + "."]);
  if (j.rows.length && j.verifier === "none")
    out.push(["warn", "Unverified: no " + j.key_name + " in .env, so eligibility comes from the feed's tags only. Read each posting for sponsorship, citizenship and class standing."]);
  if (lp && lp.feed_error) {
    const age = j.feed.age_hours;
    out.push(["err", "Could not reach the feed (" + lp.feed_error + "). Using the cached copy" + (age == null ? "." : ", last checked " + (age < 1 ? "under an hour" : plural(age, "hour")) + " ago.")]);
  }
  // One seed line at most. Which problem it is, if any, is decided in jobs/batch.py; a seed that is
  // there and has not been read yet (the state before the first Prepare) is not one.
  const seedLine = {
    missing: ["warn", "No exclusion seed: jobs-data/exclusions.seed.json is missing, so companies you applied to, banned or capped outside this app are not excluded. Copy the file in and press Prepare."],
    unreadable: ["err", "jobs-data/exclusions.seed.json could not be read" + (j.seed_imported ? ". The entries from the last good copy still exclude, but your latest change does not." : ", so nothing in it is excluded.") + " Fix the JSON and press Prepare."],
    gone: ["warn", "jobs-data/exclusions.seed.json is gone. Its entries still exclude, but the list cannot be changed until the file is back."],
    stale: ["warn", "The exclusion seed is from " + (j.seed_age || {}).taken + " (" + (j.seed_age || {}).days + " days old). Replace jobs-data/exclusions.seed.json with a fresh copy and press Prepare."],
  }[j.seed_problem];
  if (seedLine) out.push(seedLine);
  return out;
}

function shortPart(p) { return (p.points > 0 ? "+" : "") + p.points + " " + (p.label || p.text.replace(/\.$/, "")); }

function verdictPill(v) {
  if (!v || v.page_hash == null && v.eligible == null && !v.error) return null;     // never opened, or no key
  if (v.eligible === true) return h("span", { class: "stage jst-ok", title: v.summary || "" }, "Eligible");
  if (v.eligible === false) return h("span", { class: "stage jst-no", title: v.summary || "" }, "Not eligible");
  return h("span", { class: "stage jst-unknown", title: v.error || v.summary || "" }, v.readable === false ? "Unreadable" : "Unclear");
}

function jobRow(r) {
  const j = S.jobs, d = r.detail || {}, live = r.status === "shown", open = S.jOpen === r.position;
  const parts = (d.parts || []).filter((p) => p.points);
  const state = r.status === "applied" ? h("span", { class: "stage jst-applied" }, "Applied")
    : r.status === "skipped" ? h("span", { class: "stage jst-skipped" }, "Skipped")
    : d.carried_from != null ? h("span", { class: "stage jst-carried", title: "Unfinished in the previous batch, so it is first in this one" }, "Carried over") : null;
  return h("div", { class: "row" + (live ? "" : " ended") + (open ? " open" : ""), onclick: () => toggleJob(r) },
    h("div", { class: "pos" }, r.position),
    h("div", { class: "main-col" },
      h("div", { class: "co selectable" }, r.company),
      h("div", { class: "role selectable" }, r.title + (r.city ? "  ·  " + r.city : "")),
      parts.length ? h("div", { class: "meta why" }, "Score " + d.score + ":  " + parts.map(shortPart).join("  ·  ")) : null),
    h("div", { class: "acts" },
      live && j.verifier === "llm" ? h("button", { class: "ibtn", title: "Read the posting and check it against your eligibility", onclick: (e) => { e.stopPropagation(); verifyJob(r.position); } }, d.verdict ? "Verify again" : "Verify") : null,
      live ? confirmBtn("Skip", "ibtn del", async () => { if (await call("jobs_skip", [r.position])) { toast("Skipped " + r.company); loadJobs(); refresh(true); } }) : null),
    (r.same_company || []).length ? h("span", { class: "flag", title: r.same_company.map((x) => x.text).join(" ") }, "other role here") : null,
    r.resume ? h("span", { class: "chip", title: "Recommended resume" + (d.resume_why ? ": " + d.resume_why : "") }, r.resume) : null,
    verdictPill(d.verdict), state,
    live && r.url ? h("button", { class: "btn primary", title: r.url, onclick: (e) => { e.stopPropagation(); call("open_url", r.url); } }, "Apply") : null);
}

function jobDetail(r) {
  const j = S.jobs, d = r.detail || {}, v = d.verdict;
  const score = h("div", { class: "jparts" });
  for (const p of d.parts || []) score.append(h("span", { class: "pts" }, (p.points > 0 ? "+" : "") + p.points), h("span", { class: "selectable" }, p.text));
  score.append(h("span", { class: "pts tot" }, d.score), h("span", { class: "tot" }, "Score"));
  const kids = [h("div", { class: "jsect" }, "Why it ranked here"), score];
  if (d.resume_why) kids.push(h("div", { class: "jsect" }, "Resume"), h("div", { class: "selectable" }, r.resume + ": " + d.resume_why + "."));
  const flags = (d.flags || []).filter((f) => f.text).map((f) => f.text);
  for (const x of r.same_company || []) flags.push(x.text);
  if (flags.length) { kids.push(h("div", { class: "jsect" }, "Check before applying")); for (const f of flags) kids.push(h("div", { class: "selectable" }, f)); }
  kids.push(h("div", { class: "jsect" }, "Posting check"));
  if (j.verifier === "none") kids.push(h("div", {}, "Not verified. Add " + j.key_name + " to .env to have this posting read for sponsorship, citizenship, class standing and graduation window."));
  else if (!v) kids.push(h("div", {}, S.jBusy || "Not verified yet."));
  else {
    kids.push(h("div", { class: "selectable" }, (v.error ? v.error + " " : "") + (v.summary || "") + (v.pay ? "  Pay: " + v.pay + "." : "") + (v.cached ? "  (from the cache)" : "")));
    const checks = h("div", { class: "jparts" });
    for (const [k, c] of Object.entries(v.checks || {})) {
      if (c.ok == null && !c.evidence) continue;
      checks.append(h("span", { class: "pts" }, c.ok === true ? "ok" : c.ok === false ? "no" : "?"),
        h("span", { class: "selectable" }, k.replace("_", " "), c.evidence ? h("span", { class: "ev" }, c.evidence) : null));
    }
    if (checks.children.length) kids.push(checks);
  }
  kids.push(h("div", { class: "jsect" }, "Cover letter"));
  kids.push(letterBlock(r, d));
  if (r.url) kids.push(h("div", { class: "jlink selectable" }, r.url));
  return h("div", { class: "detail", onclick: (e) => e.stopPropagation() }, kids);
}

// A letter is only ever drafted on request: it costs a page fetch and a long model call, and he does not
// want one for every row. The draft is stored on the row, so it comes back when he reopens it.
function letterBlock(r, d) {
  const j = S.jobs, L = d.letter;
  if (!j.letter_key)
    return h("div", {}, "Add " + j.key_name + " to .env to draft one here.");
  if (!j.letter_profile)
    return h("div", {}, "Add jobs-data/resume.json first. A letter is written from the variant this row "
      + "picked (" + (r.resume || "?") + "), so it can only claim what that resume says.");
  const ask = h("button", { class: "btn", onclick: (e) => { e.stopPropagation(); draftLetter(r.position); } },
    L && L.text ? "Draft again" : "Draft from the posting");
  if (!L || (!L.text && !L.error))
    return h("div", { class: "jletter-head" }, ask,
      h("span", { class: "meta" }, S.jBusy || ("Uses the " + (r.resume || "?") + " resume.")));
  const bits = [h("div", { class: "jletter-head" }, ask,
    h("button", { class: "btn", onclick: async (e) => {
        e.stopPropagation();
        if (await call("copy_text", L.text)) toast("Copied");
      } }, "Copy"),
    h("span", { class: "meta" }, (L.variant || "") + " resume · " + plural(L.words || 0, "word")
      + (L.retried ? " · rewritten once" : "")))];
  if (L.error) bits.push(h("div", { class: "err selectable" }, L.error));
  for (const p of L.problems || []) bits.push(h("div", { class: "warn selectable" }, "Still reads wrong: " + p));
  if (L.text) bits.push(h("div", { class: "jletter selectable" }, L.text));
  return h("div", {}, bits);
}

async function draftLetter(position) {
  const r = await call("jobs_letter", position);
  if (!r) return;
  if (r.started) { S.jBusy = "Drafting the letter…"; renderJobs(); }
  else toast("Still working on the last request");
}

// Opening a row is the moment it is verified: one row, never the whole batch, never on render.
function toggleJob(r) {
  const opening = S.jOpen !== r.position;
  S.jOpen = opening ? r.position : null;
  renderJobs();
  const j = S.jobs;
  if (opening && r.status === "shown" && j.verifier === "llm" && !(r.detail || {}).verdict && !S.jBusy) verifyJob(r.position);
}

async function verifyJob(position) {
  const r = await call("jobs_verify", position);
  if (!r) return;
  if (r.started) { S.jBusy = "Reading posting " + position + "…"; renderJobs(); }
  else toast("Still working on the last request");
}

function renderJobs() {
  const j = S.jobs; if (!j) return;
  const open = j.unreported.length;
  const pill = $("jPill"); pill.textContent = open ? open + " open" : ""; pill.style.display = open ? "" : "none";
  const facts = $("jFacts"); facts.replaceChildren();
  for (const [cls, text] of jobFacts(j)) facts.append(h("div", { class: cls }, text));
  $("jPrepare").disabled = !!S.jBusy;
  $("jReport").disabled = !open || !!S.jBusy;
  $("jThrough").max = j.rows.length || 1;
  $("jResult").textContent = S.jReport;
  $("jClose").replaceChildren(open ? confirmBtn("Close batch", "btn danger", async () => {
    const r = await call("jobs_close_batch");
    if (r) { toast("Skipped " + plural(r.skipped.length, "row")); S.jOpen = null; loadJobs(); refresh(true); }
  }) : "");
  const box = $("jList"); box.replaceChildren();
  if (!j.rows.length) { box.append(h("div", { class: "empty" }, "Nothing to show.")); return; }
  for (const r of j.rows) { box.append(jobRow(r)); if (S.jOpen === r.position) box.append(jobDetail(r)); }
}

async function prepareJobs() {
  const j = S.jobs, btn = $("jPrepare"), open = j ? j.unreported.length : 0;
  // His rule: before a new batch, look at the last one. An unfinished batch needs a second, deliberate press.
  if (open && !btn.dataset.armed) {
    btn.dataset.armed = "1"; btn.textContent = "Carry " + open + " over?";
    setTimeout(() => { delete btn.dataset.armed; btn.textContent = "Prepare"; }, 3500);
    return;
  }
  delete btn.dataset.armed; btn.textContent = "Prepare";
  const r = await call("jobs_prepare", $("jN").value);
  if (!r) return;
  if (r.started) { S.jBusy = "Starting…"; S.jReport = ""; renderJobs(); }
  else toast("Already working");
}

async function reportJobs() {
  const n = $("jThrough").value.trim();
  if (!n) { $("jThrough").focus(); return; }
  const r = await call("jobs_report_through", n);
  if (!r) return;
  const bits = [plural(r.counted, "application") + " counted"];
  if (r.already_tracked) bits.push(r.already_tracked + " already tracked from mail");
  if (r.daily) bits.push("today " + r.daily.count + (r.daily.target ? "/" + r.daily.target : ""));
  if (r.remaining.length) bits.push(r.remaining.length + " still open");
  S.jReport = bits.join("  ·  ");
  toast(r.reported ? "Reported " + plural(r.reported, "application") : "Nothing new to report up to " + n);
  $("jThrough").value = ""; S.jOpen = null;
  loadJobs(); refresh(true);
}

async function whyJobs() {
  const q = $("jWhy").value.trim(); if (!q) return;
  const r = await call("jobs_why", q); if (!r) return;
  const label = { shown: "In a batch", excluded: "Excluded", backlog: "Backlog", waiting: "Waiting", not_in_feed: "Not in feed" };
  const box = $("jWhyOut"); box.replaceChildren();
  for (const a of r.answers)
    box.append(h("div", { class: "row jwhy" }, h("span", { class: "st" }, label[a.status] || a.status),
      h("div", { class: "main-col" }, a.title ? h("div", { class: "title selectable" }, a.company + "  ·  " + a.title) : null,
        h("div", { class: "meta selectable" }, a.text))));
}

window.onJobs = (p) => {
  if (p.progress) { S.jBusy = p.progress; if (S.jobs) renderJobs(); return; }
  if (!p.done) return;
  S.jBusy = "";
  const errs = Object.values(p.errors || {});
  if (errs.length) toast(errs.join(", "), true);
  else if (p.kind === "letter" && p.letter && p.letter.error) toast(p.letter.error, true);
  else if (p.kind === "prepare")
    toast(!p.rows ? "Nothing new and eligible" : p.created ? "Batch ready: " + plural(p.rows, "posting") + (p.carried ? ", " + p.carried + " carried over" : "") : "Nothing new; the open batch stands");
  loadJobs(); refresh(true);
};

/* ---------- mail accounts ---------- */
// One-click fills for the address field, from TODAY_ACCOUNT_HINTS in ~/.config/today/.env (see .env.example).
// No hints configured is the normal case for a fresh clone: the sentence just ends after "paste it below".
function renderHints(hints) {
  const el = $("mHints"); if (!el) return;
  el.replaceChildren();
  if (!hints.length) return;
  el.append(" Fill in: ");
  hints.forEach((a, i) => {
    if (i) el.append(" · ");
    el.append(h("button", { class: "linkbtn", onclick: () => { $("mEmail").value = a; $("mPw").focus(); } }, a));
  });
}

async function loadAccounts() {
  const box = $("accts"); box.replaceChildren(h("div", { class: "empty" }, "Checking Keychain…"));
  const r = await call("accounts"); if (!r) return;
  renderHints(r.hints || []);
  box.replaceChildren();
  if (!r.accounts.length) { box.append(h("div", { class: "empty" }, "None yet. Add one below to let Check mail find OAs, interviews and rejections.")); return; }
  for (const a of r.accounts) {
    box.append(h("div", { class: "row", style: "align-items:center" },
      h("div", { class: "main-col" }, h("div", { class: "title selectable" }, a.email),
        h("div", { class: "meta " + (a.has_password ? "ok" : "bad") }, a.has_password ? "App password saved in Keychain" : "No password saved: add it again below")),
      confirmBtn("Remove", "btn danger", async () => { if (await call("mail_remove", a.email)) { loadAccounts(); refresh(true); } })));
  }
}

/* ---------- scan ---------- */
async function scan() {
  if (S.st && S.st.scanning) return;
  const r = await call("scan");
  if (r && r.started) setScanning("Starting…");
}
function setScanning(text) { $("scanBtn").classList.add("spin"); const i = $("scanInfo"); i.className = "scaninfo"; i.textContent = text; if (S.st) S.st.scanning = true; }
window.onScan = (p) => {
  if (p.progress) { setScanning(p.progress); return; }
  if (p.done) {
    $("scanBtn").classList.remove("spin");
    const errs = Object.keys(p.errors || {});
    toast(errs.length ? "Mail check finished with problems (" + errs.join(", ") + ")" : "Mail checked: " + p.found + " relevant message" + (p.found === 1 ? "" : "s"), !!errs.length);
    if (S.st) S.st.scanning = false;
    if (p.tracker) toast((errs.length ? "Finished with problems. " : "Mail checked. ") + p.tracker.applications + " applications, " + p.tracker.active + " active, " + p.tracker.rejected + " rejected", !!errs.length);
    refresh(true);
  }
};

/* ---------- wiring ---------- */
function setView(v) {
  S.view = v;
  document.querySelectorAll(".nav button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  document.querySelectorAll(".view").forEach((s) => s.classList.toggle("on", s.id === "v-" + v));
  if (v === "mail") { loadAccounts(); loadHistorySince(); }
  if (v === "apps") loadApps();
  if (v === "jobs") loadJobs();          // shows the stored batch; Prepare is a deliberate press, never automatic
  $("main").scrollTop = 0;
}
function go(date) { S.date = date; S.editing = null; S.dailyEdit = null; refresh(true); }

function wire() {
  document.querySelectorAll(".nav button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $("dPrev").onclick = () => go(addDays(S.date, -1));
  $("dNext").onclick = () => go(addDays(S.date, 1));
  $("dToday").onclick = () => go(S.st.today);
  $("scanBtn").onclick = scan;
  $("showHidden").onchange = () => refresh(true);

  $("qAdd").addEventListener("keydown", async (e) => {
    if (e.key === "Escape") { e.target.value = ""; e.target.blur(); }
    if (e.key !== "Enter" || !e.target.value.trim()) return;
    const r = await call("plan_add", S.date, e.target.value);
    if (r) { e.target.value = ""; refresh(true); }
  });
  $("dAdd").addEventListener("keydown", async (e) => {
    if (e.key === "Escape") { e.target.value = ""; e.target.blur(); }
    if (e.key !== "Enter" || !e.target.value.trim()) return;
    if (await call("daily_add", e.target.value)) { e.target.value = ""; refresh(true); }
  });
  const addDeadline = async () => {
    if (!$("lName").value.trim()) { $("lName").focus(); return; }
    if (await call("deadline_add", $("lName").value, $("lWhen").value)) { $("lName").value = ""; $("lWhen").value = ""; refresh(true); }
  };
  $("lAdd").onclick = addDeadline;
  ["lName", "lWhen"].forEach((id) => $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") addDeadline(); }));

  document.querySelectorAll("[data-url]").forEach((b) => (b.onclick = () => call("open_url", b.dataset.url)));
  document.querySelectorAll("[data-fill]").forEach((b) => (b.onclick = () => { $("mEmail").value = b.dataset.fill; $("mPw").focus(); }));
  const addAcct = async () => {
    const btn = $("mAdd"); btn.disabled = true; btn.textContent = "Testing…";
    const r = await call("mail_add", $("mEmail").value, $("mPw").value);
    btn.disabled = false; btn.textContent = "Test & save";
    if (r) { $("mPw").value = ""; toast("Saved " + $("mEmail").value + (r.saved === "keychain" ? " (Keychain)" : "")); $("mEmail").value = ""; loadAccounts(); refresh(true); }
  };
  $("mAdd").onclick = addAcct;
  $("hSave").onclick = async () => { if (await call("set_history_since", $("hSince").value)) toast("Saved"); };
  $("aRescan").onclick = async () => {
    const r = await call("scan", true);
    if (r && r.started) { setScanning("Re-reading mail history…"); toast("Rescanning history. This can take a few minutes."); }
  };
  $("aSearch").addEventListener("input", renderApps);
  $("aSort").addEventListener("change", renderApps);
  $("mPw").addEventListener("keydown", (e) => { if (e.key === "Enter") addAcct(); });

  $("jPrepare").onclick = prepareJobs;
  $("jReport").onclick = reportJobs;
  $("jThrough").addEventListener("keydown", (e) => { if (e.key === "Enter") reportJobs(); });
  $("jWhy").addEventListener("keydown", (e) => { if (e.key === "Enter") whyJobs(); if (e.key === "Escape") { e.target.value = ""; $("jWhyOut").replaceChildren(); } });

  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    if (e.metaKey && ["1", "2", "3", "4", "5"].includes(e.key)) { e.preventDefault(); setView(["plan", "inbox", "apps", "mail", "jobs"][+e.key - 1]); return; }
    if (e.metaKey && e.key.toLowerCase() === "r") { e.preventDefault(); scan(); return; }
    if (e.metaKey && e.key.toLowerCase() === "n") { e.preventDefault(); setView("plan"); $("qAdd").focus(); return; }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "Escape" && (S.editing || S.dailyEdit)) { S.editing = null; S.dailyEdit = null; render(); return; }
    if (S.view !== "plan") return;
    if (e.key === "n") { e.preventDefault(); $("qAdd").focus(); }
    else if (e.key === "ArrowLeft") go(addDays(S.date, -1));
    else if (e.key === "ArrowRight") go(addDays(S.date, 1));
    else if (e.key === "t" && S.st && !S.st.is_today) go(S.st.today);
  });
}

/* ---------- keep current: files changed by the CLI, midnight, countdowns ---------- */
async function tick() {
  const v = await window.pywebview.api.version().catch(() => null);
  if (v && !v.error) {
    const today = v.v.split("|")[0];
    if (S.st && today !== S.st.today) {           // midnight: follow the new day if we were on "today"
      const wasToday = S.st.is_today;
      if (wasToday) { S.date = today; toast("New day"); }
      return refresh(true);
    }
    if (v.v !== S.lastV || Date.now() - S.lastRender > 60000) refresh(false);
    if (v.scanning && S.st && !S.st.scanning) setScanning("Checking mail…");
    if (v.jobs_running && !S.jBusy && S.view === "jobs") { S.jBusy = "Working…"; if (S.jobs) renderJobs(); }
  }
}

window.addEventListener("error", (e) => { try { window.pywebview.api.log_js(e.message + " @" + e.lineno + ":" + e.colno); } catch (_) {} });
window.addEventListener("unhandledrejection", (e) => { try { window.pywebview.api.log_js("promise: " + (e.reason && (e.reason.stack || e.reason))); } catch (_) {} });

async function init() {
  if (S.inited) return; S.inited = true;
  wire();
  await refresh(true);
  if (S.st && S.st.scanning) setScanning("Checking mail…");
  setInterval(tick, 3000);
}
if (window.pywebview && window.pywebview.api) init();
else window.addEventListener("pywebviewready", init);
