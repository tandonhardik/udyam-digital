// frontend/partner.js  - back-office for unfinished leads. All user-derived text is HTML-escaped.
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const COLS = 6;

function token() { try { return sessionStorage.getItem('partner_token') || ''; } catch (e) { return ''; } }
function setToken() {
    const t = prompt('Partner token (printed in the server console, or PARTNER_TOKEN in .env):', token());
    if (t === null) return;
    try { sessionStorage.setItem('partner_token', t.trim()); } catch (e) {}
    loadLeads();
}
async function api(path) {
    const r = await fetch(path, { headers: { 'X-Partner-Token': token() } });
    if (r.status === 401) { const e = new Error('Not authorised - click "Token" and enter the partner token'); e.auth = true; throw e; }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
}
function msgRow(text, color) { return `<tr><td colspan="${COLS}" style="text-align:center;${color ? 'color:' + color : ''}">${esc(text)}</td></tr>`; }

async function loadLeads() {
    const tbody = document.getElementById('leads-body');
    tbody.innerHTML = msgRow('Loading leads...');
    try {
        try {
            const m = await api('/api/partner/model');
            document.getElementById('model-info').textContent = m.cv_auc
                ? `Scoring: ${m.model} · cross-validated AUC ${m.cv_auc} · top-decile lift ${m.top_decile_lift}x · trained on ${m.trained_on} leads`
                : `Scoring: ${m.model}. ${m.detail || ''}`;
        } catch (e) { if (e.auth) throw e; }
        const leads = await api('/api/partner/leads');
        if (!leads.length) { tbody.innerHTML = msgRow('No unfinished leads. Open the main app or /whatsapp and chat with the bot.'); return; }
        tbody.innerHTML = leads.map(l => `
            <tr>
                <td><span class="badge ${esc(l.priority.toLowerCase())}">${esc(l.score)}% · ${esc(l.priority)}</span></td>
                <td><b>${esc(l.business_type)}</b><br><small style="color:#6b6b70">${esc(l.segment)} · Tier ${esc(l.city_tier)} · ${esc(l.employees)} staff · ${l.has_gstin ? 'GSTIN' : 'No GSTIN'}</small><br>
                    <small style="color:#6b6b70">${esc(l.needs)}${l.phone ? ' · ' + esc(l.phone) : ''} · ${esc(l.channel)}</small></td>
                <td>${esc(l.stage)}<br><span class="badge ${l.status === 'Dropped' ? 'dropped' : 'active'}">${esc(l.status)}</span>
                    <small style="color:#6b6b70"> ${esc(l.idle_minutes)} min idle</small>${l.stuck_reason ? `<br><small style="color:#b00020">Stuck: ${esc(l.stuck_reason)}</small>` : ''}</td>
                <td class="reasons">${(l.reasons || []).map(esc).join('<br>')}</td>
                <td style="font-size:13px">${esc(l.next_action)}</td>
                <td><button class="primary" style="padding:6px 12px;font-size:12px;width:auto" data-ref="${esc(l.ref)}">View Chat</button></td>
            </tr>`).join('');
        tbody.querySelectorAll('button[data-ref]').forEach(b => b.onclick = () => viewTranscript(b.dataset.ref));
    } catch (e) {
        tbody.innerHTML = msgRow(e.message, 'red');
        if (e.auth) setTimeout(() => { if (!token()) setToken(); }, 50);
    }
}

async function viewTranscript(ref) {
    const modal = document.getElementById('modal'), body = document.getElementById('modal-body');
    modal.style.display = 'flex';
    body.innerHTML = '<div style="text-align:center;padding:20px">Loading context...</div>';
    try {
        const data = await api(`/api/partner/leads/${encodeURIComponent(ref)}/transcript`);
        body.innerHTML = data.transcript.map(m => `<div class="msg ${m.role === 'user' ? 'me' : 'bot'}">${esc(m.text)}</div>`).join('');
    } catch (e) {
        body.innerHTML = `<div class="fail">Failed to load transcript: ${esc(e.message)}</div>`;
    }
}

loadLeads();