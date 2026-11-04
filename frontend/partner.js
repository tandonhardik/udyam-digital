// frontend/partner.js

async function loadLeads() {
    const tbody = document.getElementById('leads-body');
    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center;">Loading leads...</td></tr>';
    
    try {
        const response = await fetch('/api/partner/leads');
        const leads = await response.json();
        
        if (leads.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center;">No active leads in the funnel. Open the main app and chat with the bot!</td></tr>';
            return;
        }

        tbody.innerHTML = leads.map(lead => `
            <tr>
                <td>
                    <span class="badge ${lead.priority.toLowerCase()}">${lead.score}%</span>
                </td>
                <td>
                    <b>${lead.business_type}</b><br>
                    <small style="color: #6b6b70;">${lead.segment.toUpperCase()}</small>
                </td>
                <td>Tier ${lead.city_tier} <br><small style="color: ${lead.has_gstin ? '#0a7a3d' : '#b00020'}">${lead.has_gstin ? 'Has GSTIN' : 'No GSTIN'}</small></td>
                <td>${lead.needs}</td>
                <td>
                    <button class="primary" style="padding: 6px 12px; font-size: 12px; width: auto;" onclick="viewTranscript('${lead.session_id}')">
                        View Chat
                    </button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" style="color: red; text-align: center;">Error loading leads: ${e.message}</td></tr>`;
    }
}

async function viewTranscript(sessionId) {
    const modal = document.getElementById('modal');
    const modalBody = document.getElementById('modal-body');
    
    modal.style.display = 'flex';
    modalBody.innerHTML = '<div style="text-align: center; padding: 20px;">Loading context...</div>';
    
    try {
        const response = await fetch(`/api/partner/leads/${sessionId}/transcript`);
        const data = await response.json();
        
        modalBody.innerHTML = data.transcript.map(msg => `
            <div class="msg ${msg.role === 'user' ? 'me' : 'bot'}">
                ${msg.text}
            </div>
        `).join('');
    } catch (e) {
        modalBody.innerHTML = `<div class="fail">Failed to load transcript: ${e.message}</div>`;
    }
}

// Load leads immediately on page load
loadLeads();