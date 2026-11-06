const $ = s => document.querySelector(s);
const S = { sid: null, profile: null, items: [], addons: [], gstinStatus: null, orderId: null };
const inr = n => "₹" + Math.round(n).toLocaleString("en-IN");
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const GREETING = "Namaste! I'm your Accel Business advisor. Tell me about your business: what do you run, and where?\nनमस्ते! अपने व्यापार के बारे में बताइए।";

function saveState() { try { sessionStorage.setItem("udyam_state", JSON.stringify(S)); } catch (e) {} }
function loadState() { try { return JSON.parse(sessionStorage.getItem("udyam_state") || "null"); } catch (e) { return null; } }
function clearState() { try { sessionStorage.removeItem("udyam_state"); } catch (e) {} }

// Navigation function to simulate physically separate pages
function navigate(page) {
  saveState();                                                         // Save memory
  const q = new URLSearchParams({ page });
  if (S.sid) q.set("session", S.sid);
  window.location.href = "/?" + q.toString();                          // Hard reload the browser
}

// The server keeps sessions in memory, so a restart (or an old browser tab) makes our saved session unknown.
function sessionLost() {
  clearState();
  toast("Your session expired. Starting again…");
  setTimeout(() => { window.location.href = "/"; }, 1800);
}
const isSessionLost = e => e && e.status === 404 && /session/i.test(e.message || "");

function show(id) {
  document.querySelectorAll("main > section").forEach(s => s.hidden = s.id !== id);
  document.querySelectorAll("#steps i").forEach(i => i.classList.toggle("on", i.dataset.s === id));
  $("#chatForm").style.display = id === "advisor" ? "flex" : "none";
}
function toast(t) { const el = $("#toast"); el.textContent = t; el.classList.add("show"); setTimeout(() => el.classList.remove("show"), 2200); }
async function api(path, opts) {
  const r = await fetch(path, opts); const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(typeof j.detail === "string" ? j.detail : "Request failed"), { detail: j.detail, status: r.status });
  return j;
}
const post = (p, b) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });

// ---------- Advisor ----------
function bubble(text, who) { const d = document.createElement("div"); d.className = "msg " + who; d.textContent = text; $("#chat").append(d); d.scrollIntoView(); }
async function send(text) {
  bubble(text, "me");
  try {
    const r = await post("/api/chat", { session_id: S.sid, message: text });
    S.sid = r.session_id; saveState(); bubble(r.message, "bot");
    if (r.done) {
      S.profile = r.profile; S.items = r.recommendation.items; S.addons = r.recommendation.addons;
      S.gstinStatus = r.profile.has_gstin ? "Active" : null;
      saveState();

      const btn = document.createElement("button");
      btn.className = "primary";
      btn.style.marginTop = "12px";
      btn.style.width = "fit-content";
      btn.textContent = "View My Quote";
      btn.onclick = () => navigate("quote");                           // physical page reload

      $("#chat").append(btn);
      btn.scrollIntoView();
    }
  } catch (e) { bubble("Sorry, something went wrong: " + e.message, "bot"); }
}
$("#chatForm").onsubmit = e => { e.preventDefault(); const v = $("#chatIn").value.trim(); if (v) { $("#chatIn").value = ""; send(v); } };
$("#langBtn").onclick = () => send("हिंदी में बात करें");

// ---------- Quote ----------
async function renderQuote() {
  const p = S.profile;
  const q = await post("/api/quote", { items: S.items, employees: p.employees, sites: p.sites, city_tier: p.city_tier, gstin_status: S.gstinStatus });
  $("#lines").innerHTML = q.lines.map(l => `<div class="row"><div>${esc(l.name)}<small>${esc(l.qty)} × ${esc(l.unit || "unit")} · ${esc(l.contract_months)}-month contract · setup ${inr(l.setup)}</small>
    <span class="qty"><button data-q="${esc(l.product_id)}" data-d="-1">−</button> ${esc(l.qty)} <button data-q="${esc(l.product_id)}" data-d="1">+</button> <button data-rm="${esc(l.product_id)}" title="Remove">✕</button></span></div><div>${inr(l.monthly)}/mo</div></div>`).join("");
  $("#lines").querySelectorAll("button").forEach(b => b.onclick = () => {
    if (b.dataset.rm) S.items = S.items.filter(i => i.product_id !== b.dataset.rm);
    else S.items = S.items.map(i => i.product_id === b.dataset.q ? { ...i, qty: Math.max(1, i.qty + +b.dataset.d) } : i);
    saveState();
    renderQuote();
  });
  $("#addons").innerHTML = S.addons.length ? "<h3>Optional add-ons</h3>" + S.addons.map(a =>
    `<label class="addon"><input type="checkbox" data-id="${esc(a.product_id)}" data-qty="${esc(a.qty)}" ${S.items.some(i => i.product_id === a.product_id) ? "checked" : ""}> ${esc(a.name)} <small>from +${inr(a.monthly)}/${esc((a.unit || "unit").replace("per ", ""))}/mo</small></label>`).join("") : "";
  $("#addons").querySelectorAll("input").forEach(cb => cb.onchange = () => {
    S.items = cb.checked ? [...S.items, { product_id: cb.dataset.id, qty: +cb.dataset.qty || 1 }] : S.items.filter(i => i.product_id !== cb.dataset.id);
    saveState();
    renderQuote();
  });
  $("#totals").innerHTML = `<div class="card"><div class="row"><span>Monthly</span><span>${inr(q.monthly_total)}</span></div>
    <div class="row"><span>GST (18%)</span><span>${inr(q.gst_on_monthly)}</span></div>
    <div class="row tot"><span>Total per month</span><span>${inr(q.monthly_with_gst)}</span></div>
    <div class="row"><span>One-time setup</span><span>${inr(q.setup_total)}</span></div>
    <div class="row"><span>Contract</span><span>${esc(q.contract_months)} months</span></div></div>`;
  const v = Object.values(q.violations).flat();
  $("#violations").innerHTML = v.map(x => "⚠ " + esc(x)).join("<br>");
  $("#toKyc").disabled = !q.valid;
}
$("#toKyc").onclick = () => navigate("kyc");                           // Reloads page to KYC

// ---------- Consent + KYC ----------
const syncConsent = () => $("#consentBtn").disabled = !($("#c1").checked && $("#c2").checked);
$("#c1").onchange = $("#c2").onchange = syncConsent;
$("#consentBtn").onclick = async () => {
  try {
    await post("/api/consent", { session_id: S.sid, purposes: ["kyc_verification", "service_provisioning"], accepted: true });
    $("#consent").hidden = true; $("#upload").hidden = false;
  } catch (e) { if (isSessionLost(e)) sessionLost(); else toast(e.message); }
};
$("#verifyBtn").onclick = async () => {
  const fs = $("#files").files;
  if (!fs.length) return toast("Choose your document photos first");

  const fd = new FormData();
  [...fs].forEach(f => fd.append("files", f));

  $("#kycOut").textContent = "Reading documents…";

  try {
    const r = await api(`/api/kyc/verify?session_id=${encodeURIComponent(S.sid)}`, { method: "POST", body: fd });

    const docs = r.documents.map(d => `<div class="row"><span>${esc(d.file)}</span><span>${esc(d.type)}</span></div>`).join("");
    // Issues tell the user exactly why a document was rejected
    const issues = r.issues.map(i => `<div class="${i.severity === "error" ? "fail" : "warn"}">${i.severity === "info" ? "ℹ" : i.severity === "error" ? "✖" : "⚠"} ${esc(i.message)}</div>`).join("");
    const successMsg = r.verdict === "approved" ? `<div class="pass" style="margin-top: 10px; font-weight: bold;">✔ All business documents verified successfully.</div>` : "";

    $("#kycOut").innerHTML = `<h3 class="${r.verdict === "approved" ? "pass" : "fail"}">${esc(r.verdict.toUpperCase())}</h3>${docs}${successMsg}${issues}`;

    if (r.gst_status) S.gstinStatus = r.gst_status;
    saveState();

    // Only show the "Place order" button if KYC is approved
    $("#placeBtn").hidden = r.verdict !== "approved";

    // If approved, verify that the active quote is still valid given the verified GST status
    if (r.verdict === "approved") {
      const p = S.profile;
      const q = await post("/api/quote", { items: S.items, employees: p.employees, sites: p.sites, city_tier: p.city_tier, gstin_status: S.gstinStatus });
      if (!q.valid) {
        $("#placeBtn").hidden = true;
        $("#kycOut").innerHTML += `<div class="fail" style="margin-top: 12px;"><b>Your verified details don't allow some items:</b><br>${Object.values(q.violations).flat().map(esc).join("<br>")}
          <br><button class="ghost" id="backQuote" style="margin-top: 8px;">Back to quote</button></div>`;
        $("#backQuote").onclick = () => navigate("quote");
      }
    }
  } catch (e) {
    if (isSessionLost(e)) return sessionLost();
    $("#kycOut").innerHTML = `<div class="fail">${esc(e.message)}</div>`;
  }
};
$("#placeBtn").onclick = async () => {
  try {
    // The server prices the order from its own record of the business; only the chosen items are sent.
    const o = await post("/api/order", { session_id: S.sid, items: S.items });
    S.orderId = o.order_id; saveState(); navigate("track");            // Reloads page to Track
  } catch (e) {
    if (isSessionLost(e)) return sessionLost();
    const v = e.detail && e.detail.violations ? Object.values(e.detail.violations).flat().join("; ") : "";
    toast(e.message + " " + v);
  }
};

// ---------- Tracker ----------
async function renderTrack(ff) {
  const d = new Date("2026-07-15T00:00:00Z"); d.setUTCDate(d.getUTCDate() + ff);
  const s = await api(`/api/order/${encodeURIComponent(S.orderId)}?as_of=${d.toISOString().slice(0, 10)}`);
  $("#orderInfo").innerHTML = `<div class="card"><b>${esc(s.order_id)}</b><br>Status: <b>${esc(s.current_stage)}</b><br>Expected activation: ${esc(s.expected_activation)} (${esc(s.days_remaining)} days to go)</div>`;
  let cur = s.stages.map(x => x.done).lastIndexOf(true);
  $("#timeline").innerHTML = s.stages.map((x, i) => `<div class="step ${x.done ? "done" : ""} ${i === cur ? "cur" : ""}"><div class="dot"></div><div><b>${esc(x.name)}</b><br><small>${esc(x.expected_date)} · ${esc(x.note)}</small></div></div>`).join("");
}
$("#ff").oninput = e => { $("#ffv").textContent = e.target.value == 0 ? "today" : `+${e.target.value} days`; renderTrack(+e.target.value); };

// ---------- App Initialization & Routing ----------
async function init() {
  const urlParams = new URLSearchParams(window.location.search);
  let targetPage = urlParams.get("page") || "advisor";
  const urlSid = urlParams.get("session");

  // 1. Load saved state (standard flow: every navigate() reloads the page)
  const saved = loadState();
  if (saved) Object.assign(S, saved);

  // 2. Arriving from a WhatsApp link for a DIFFERENT session: drop the old browser state, it belongs to someone else
  if (urlSid && urlSid !== "null" && urlSid !== S.sid) {
    Object.assign(S, { sid: urlSid, profile: null, items: [], addons: [], gstinStatus: null, orderId: null });
  }

  // 3. HYDRATE: memory is empty (WhatsApp hand-off, or a fresh tab), so ask the server what it knows
  if (S.sid && (targetPage === "quote" || targetPage === "kyc") && !S.profile) {
    try {
      const data = await api(`/api/session/${encodeURIComponent(S.sid)}`);
      S.profile = data.profile;
      S.items = data.recommendation.items;
      S.addons = data.recommendation.addons;
      S.gstinStatus = data.profile.has_gstin ? "Active" : null;
      saveState();
    } catch (e) {
      console.error("Hydration failed", e);
      if (isSessionLost(e)) return sessionLost();
    }
  }

  // 4. Pages that need data we do not have go back to the start instead of crashing
  if ((targetPage === "quote" || targetPage === "kyc") && !S.profile) targetPage = "advisor";
  if (targetPage === "track" && !S.orderId) targetPage = S.profile ? "quote" : "advisor";

  show(targetPage);

  // 5. Page-specific rendering
  if (targetPage === "advisor") {
    bubble(GREETING, "bot");
  } else if (targetPage === "quote") {
    renderQuote();
  } else if (targetPage === "kyc") {
    api("/api/consent/notice").then(n => {
      $("#consentBody").innerHTML = `<p>${n.purposes.map(p => "• " + esc(p.text)).join("<br>")}</p>
        <p><b>We collect:</b> ${n.data_collected.map(esc).join(", ")}.<br><b>We do not collect:</b> ${n.not_collected.map(esc).join(", ")}.</p>
        <p><b>Retention:</b> ${esc(n.retention)}</p><p><small>${esc(n.rights)}</small></p>`;
    });
  } else if (targetPage === "track") {
    renderTrack(0);
  }
}

init();