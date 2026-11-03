const $ = s => document.querySelector(s);
const S = { sid: null, profile: null, items: [], addons: [], gstinStatus: null, orderId: null };
const inr = n => "₹" + Math.round(n).toLocaleString("en-IN");

function show(id) {
  document.querySelectorAll("main > section").forEach(s => s.hidden = s.id !== id);
  document.querySelectorAll("#steps i").forEach(i => i.classList.toggle("on", i.dataset.s === id));
  $("#chatForm").style.display = id === "advisor" ? "flex" : "none";
}
function toast(t) { const el = $("#toast"); el.textContent = t; el.classList.add("show"); setTimeout(() => el.classList.remove("show"), 2200); }
async function api(path, opts) {
  const r = await fetch(path, opts); const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(typeof j.detail === "string" ? j.detail : "Request failed"), { detail: j.detail });
  return j;
}
const post = (p, b) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });

// ---------- Advisor ----------
function bubble(text, who) { const d = document.createElement("div"); d.className = "msg " + who; d.textContent = text; $("#chat").append(d); d.scrollIntoView(); }
async function send(text) {
  bubble(text, "me");
  try {
    const r = await post("/api/chat", { session_id: S.sid, message: text });
    S.sid = r.session_id; bubble(r.message, "bot");
    if (r.done) {
      S.profile = r.profile; S.items = r.recommendation.items; S.addons = r.recommendation.addons;
      S.gstinStatus = r.profile.has_gstin ? "Active" : null;
      setTimeout(() => { show("quote"); renderQuote(); }, 900);
    }
  } catch (e) { bubble("Sorry, something went wrong: " + e.message, "bot"); }
}
$("#chatForm").onsubmit = e => { e.preventDefault(); const v = $("#chatIn").value.trim(); if (v) { $("#chatIn").value = ""; send(v); } };
$("#langBtn").onclick = () => send("हिंदी में बात करें");

// ---------- Quote ----------
async function renderQuote() {
  const p = S.profile;
  const q = await post("/api/quote", { items: S.items, employees: p.employees, sites: p.sites, city_tier: p.city_tier, gstin_status: S.gstinStatus });
  $("#lines").innerHTML = q.lines.map(l => `<div class="row"><div>${l.name}<small>${l.qty} × ${l.unit || "unit"} · ${l.contract_months}-month contract · setup ${inr(l.setup)}</small>
    <span class="qty"><button data-q="${l.product_id}" data-d="-1">−</button> ${l.qty} <button data-q="${l.product_id}" data-d="1">+</button> <button data-rm="${l.product_id}" title="Remove">✕</button></span></div><div>${inr(l.monthly)}/mo</div></div>`).join("");
  $("#lines").querySelectorAll("button").forEach(b => b.onclick = () => {
    if (b.dataset.rm) S.items = S.items.filter(i => i.product_id !== b.dataset.rm);
    else S.items = S.items.map(i => i.product_id === b.dataset.q ? { ...i, qty: Math.max(1, i.qty + +b.dataset.d) } : i);
    renderQuote();
  });
  $("#addons").innerHTML = S.addons.length ? "<h3>Optional add-ons</h3>" + S.addons.map(a =>
    `<label class="addon"><input type="checkbox" data-id="${a.product_id}" data-qty="${a.qty}" ${S.items.some(i => i.product_id === a.product_id) ? "checked" : ""}> ${a.name} <small>from +${inr(a.monthly)}/${(a.unit || "unit").replace("per ", "")}/mo</small></label>`).join("") : "";
  $("#addons").querySelectorAll("input").forEach(cb => cb.onchange = () => {
    S.items = cb.checked ? [...S.items, { product_id: cb.dataset.id, qty: +cb.dataset.qty || 1 }] : S.items.filter(i => i.product_id !== cb.dataset.id);
    renderQuote();
  });
  $("#totals").innerHTML = `<div class="card"><div class="row"><span>Monthly</span><span>${inr(q.monthly_total)}</span></div>
    <div class="row"><span>GST (18%)</span><span>${inr(q.gst_on_monthly)}</span></div>
    <div class="row tot"><span>Total per month</span><span>${inr(q.monthly_with_gst)}</span></div>
    <div class="row"><span>One-time setup</span><span>${inr(q.setup_total)}</span></div>
    <div class="row"><span>Contract</span><span>${q.contract_months} months</span></div></div>`;
  const v = Object.values(q.violations).flat();
  $("#violations").innerHTML = v.map(x => "⚠ " + x).join("<br>");
  $("#toKyc").disabled = !q.valid;
}
$("#toKyc").onclick = async () => {
  show("kyc");
  const n = await api("/api/consent/notice");
  $("#consentBody").innerHTML = `<p>${n.purposes.map(p => "• " + p.text).join("<br>")}</p>
    <p><b>We collect:</b> ${n.data_collected.join(", ")}.<br><b>We do not collect:</b> ${n.not_collected.join(", ")}.</p>
    <p><b>Retention:</b> ${n.retention}</p><p><small>${n.rights}</small></p>`;
};
// ---------- Consent + KYC ----------
const syncConsent = () => $("#consentBtn").disabled = !($("#c1").checked && $("#c2").checked);
$("#c1").onchange = $("#c2").onchange = syncConsent;
$("#consentBtn").onclick = async () => {
  await post("/api/consent", { session_id: S.sid, purposes: ["kyc_verification", "service_provisioning"], accepted: true });
  $("#consent").hidden = true; $("#upload").hidden = false;
};
$("#digi").onclick = () => toast("DigiLocker integration is on the roadmap");
$("#verifyBtn").onclick = async () => {
  const fs = $("#files").files; if (!fs.length) return toast("Choose your document photos first");
  const fd = new FormData(); [...fs].forEach(f => fd.append("files", f));
  $("#kycOut").textContent = "Reading documents…";
  try {
    const r = await api(`/api/kyc/verify?session_id=${S.sid}`, { method: "POST", body: fd });
    const docs = r.documents.map(d => `<div class="row"><span>${d.file}</span><span>${d.type}</span></div>`).join("");
    const checks = r.checks.map(c => `<div class="pass">✔ ${c.label}</div>`).join("");
    const issues = r.issues.map(i => `<div class="${i.severity === "error" ? "fail" : "warn"}">${i.severity === "info" ? "ℹ" : i.severity === "error" ? "✖" : "⚠"} ${i.message}</div>`).join("");
    $("#kycOut").innerHTML = `<h3 class="${r.verdict === "approved" ? "pass" : "fail"}">${r.verdict.toUpperCase()}</h3>${docs}${checks}${issues}`;
    if (r.gst_status) S.gstinStatus = r.gst_status;
    $("#placeBtn").hidden = r.verdict !== "approved";
    if (r.verdict === "approved") {
      const p = S.profile;
      const q = await post("/api/quote", { items: S.items, employees: p.employees, sites: p.sites, city_tier: p.city_tier, gstin_status: S.gstinStatus });
      if (!q.valid) {
        $("#placeBtn").hidden = true;
        $("#kycOut").innerHTML += `<div class="fail"><b>Your verified details don't allow some items:</b><br>${Object.values(q.violations).flat().join("<br>")}
          <br><button class="ghost" id="backQuote">Back to quote</button></div>`;
        $("#backQuote").onclick = () => { show("quote"); renderQuote(); };
      }
    }
  } catch (e) { $("#kycOut").innerHTML = `<div class="fail">${e.message}</div>`; }
};
$("#placeBtn").onclick = async () => {
  const p = S.profile;
  try {
    const o = await post("/api/order", { session_id: S.sid, items: S.items, employees: p.employees, sites: p.sites, city_tier: p.city_tier });
    S.orderId = o.order_id; show("track"); renderTrack(0);
  } catch (e) {
    const v = e.detail && e.detail.violations ? Object.values(e.detail.violations).flat().join("; ") : "";
    toast(e.message + " " + v);
  }
};
// ---------- Tracker ----------
async function renderTrack(ff) {
  const d = new Date("2026-07-15T00:00:00Z"); d.setUTCDate(d.getUTCDate() + ff);
  const s = await api(`/api/order/${S.orderId}?as_of=${d.toISOString().slice(0, 10)}`);
  $("#orderInfo").innerHTML = `<div class="card"><b>${s.order_id}</b><br>Status: <b>${s.current_stage}</b><br>Expected activation: ${s.expected_activation} (${s.days_remaining} days to go)</div>`;
  let cur = s.stages.map(x => x.done).lastIndexOf(true);
  $("#timeline").innerHTML = s.stages.map((x, i) => `<div class="step ${x.done ? "done" : ""} ${i === cur ? "cur" : ""}"><div class="dot"></div><div><b>${x.name}</b><br><small>${x.expected_date} · ${x.note}</small></div></div>`).join("");
}
$("#ff").oninput = e => { $("#ffv").textContent = e.target.value == 0 ? "today" : `+${e.target.value} days`; renderTrack(+e.target.value); };

show("advisor"); bubble("Namaste! I'm your Accel Business advisor. Tell me about your business: what do you run, and where?\nनमस्ते! अपने व्यापार के बारे में बताइए।", "bot");
