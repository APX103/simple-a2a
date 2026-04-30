const API_BASE = '';

let currentRoute = '';
let agentsCache = [];

function $(sel) { return document.querySelector(sel); }
function $$ (sel) { return document.querySelectorAll(sel); }

function formatTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', { hour12: false });
}

function timeAgo(iso) {
  if (!iso) return '-';
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  if (s < 86400) return `${Math.floor(s/3600)}h ago`;
  return `${Math.floor(s/86400)}d ago`;
}

async function api(path) {
  const r = await fetch(API_BASE + path);
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function apiPost(path, body) {
  const r = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// ---------- Routes ----------

const routes = {
  '#/': renderDashboard,
  '#/agents': renderAgents,
  '#/messages': renderMessages,
  '#/stats': renderStats,
};

function navigate() {
  const hash = location.hash || '#/';
  currentRoute = hash;
  $$('nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === hash));
  const fn = routes[hash] || routes['#/'];
  fn();
}

window.addEventListener('hashchange', navigate);

// ---------- Dashboard ----------

async function renderDashboard() {
  $('#page-title').textContent = 'Dashboard';
  const main = $('main');
  main.innerHTML = '<div class="empty-state">Loading...</div>';
  try {
    const stats = await api('/admin/stats');
    const recentMsgs = await api('/admin/messages');
    main.innerHTML = `
      <div class="card-grid">
        <div class="card"><div class="card-label">在线 Agent</div><div class="card-value success">${stats.online_agents || 0}</div></div>
        <div class="card"><div class="card-label">总 Agent</div><div class="card-value">${stats.total_agents || 0}</div></div>
        <div class="card"><div class="card-label">总消息</div><div class="card-value">${stats.total_messages || 0}</div></div>
        <div class="card"><div class="card-label">未读消息</div><div class="card-value warning">${stats.unread_messages || 0}</div></div>
        <div class="card"><div class="card-label">今日消息</div><div class="card-value">${stats.messages_today || 0}</div></div>
        <div class="card"><div class="card-label">平均读取延迟</div><div class="card-value">${(stats.avg_read_latency_ms || 0).toFixed(0)}ms</div></div>
      </div>
      <div class="section-title">最近消息流</div>
      <div class="timeline">${renderMsgList(recentMsgs.slice(0, 10))}</div>
    `;
  } catch (e) {
    main.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
  }
}

// ---------- Agents ----------

async function renderAgents() {
  $('#page-title').textContent = 'Agent 列表';
  const main = $('main');
  main.innerHTML = '<div class="empty-state">Loading...</div>';
  try {
    agentsCache = await api('/admin/agents');
    main.innerHTML = `
      <div class="filter-bar">
        <input type="text" id="agent-search" placeholder="搜索 name / labels..." oninput="filterAgents()">
      </div>
      <table><thead><tr>
        <th>Name</th><th>Labels</th><th>Capabilities</th><th>Status</th><th>Unread</th><th>Last Seen</th><th>Actions</th>
      </tr></thead><tbody id="agent-tbody"></tbody></table>
    `;
    filterAgents();
  } catch (e) {
    main.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
  }
}

function filterAgents() {
  const q = ($('#agent-search')?.value || '').toLowerCase();
  const tbody = $('#agent-tbody');
  const filtered = agentsCache.filter(a =>
    (a.name || '').toLowerCase().includes(q) ||
    (a.labels || []).join(' ').toLowerCase().includes(q)
  );
  tbody.innerHTML = filtered.map(a => `
    <tr>
      <td><strong>${escapeHtml(a.name)}</strong><br><span style="font-size:0.75rem;color:var(--text-muted)">${a.agent_id}</span></td>
      <td>${(a.labels || []).map(l => `<span class="badge">${escapeHtml(l)}</span>`).join('')}</td>
      <td>${(a.capabilities || []).map(c => `<span class="badge">${escapeHtml(c)}</span>`).join('')}</td>
      <td><span class="badge ${a.online ? 'online' : 'offline'}">${a.online ? 'Online' : 'Offline'}</span></td>
      <td>${a.unread_count || 0}</td>
      <td>${timeAgo(a.last_seen)}</td>
      <td>
        <button class="btn" onclick="showAgentDetail('${a.agent_id}')">详情</button>
        <button class="btn" onclick="editAgentLabels('${a.agent_id}')">编辑 Label</button>
      </td>
    </tr>
  `).join('') || '<tr><td colspan="7" class="empty-state">无数据</td></tr>';
}

async function showAgentDetail(agentId) {
  const agent = agentsCache.find(a => a.agent_id === agentId);
  if (!agent) return;
  let recent = '';
  try {
    const detail = await api(`/admin/agents/${agentId}`);
    recent = (detail.recent_messages || []).map(m => renderMsgItem(m)).join('');
  } catch (e) { recent = `<div class="empty-state">${e.message}</div>`; }

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal">
      <h3>${escapeHtml(agent.name)} <span style="font-size:0.8rem;color:var(--text-muted)">${agent.agent_id}</span></h3>
      <div class="modal-row"><label>Announcement</label><value>${escapeHtml(agent.announcement || '-')}</value></div>
      <div class="modal-row"><label>Capabilities</label><value>${(agent.capabilities || []).map(c => `<span class="badge">${escapeHtml(c)}</span>`).join('')}</value></div>
      <div class="modal-row"><label>Limitations</label><value>${(agent.limitations || []).map(c => `<span class="badge">${escapeHtml(c)}</span>`).join('')}</value></div>
      <div class="modal-row"><label>Labels</label><value>${(agent.labels || []).map(c => `<span class="badge">${escapeHtml(c)}</span>`).join('')}</value></div>
      <div class="modal-row"><label>Registered</label><value>${formatTime(agent.registered_at)}</value></div>
      <div class="modal-row"><label>Last Seen</label><value>${formatTime(agent.last_seen)}</value></div>
      <div style="margin-top:16px;"><strong>最近消息</strong></div>
      <div class="timeline" style="margin-top:8px;">${recent}</div>
      <div style="margin-top:16px;text-align:right;">
        <button class="btn" onclick="this.closest('.modal-overlay').remove()">关闭</button>
      </div>
    </div>
  `;
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
}

async function editAgentLabels(agentId) {
  const agent = agentsCache.find(a => a.agent_id === agentId);
  if (!agent) return;
  const newLabels = prompt('编辑 Labels（逗号分隔）:', (agent.labels || []).join(', '));
  if (newLabels === null) return;
  try {
    await apiPost(`/admin/agents/${agentId}`, { labels: newLabels.split(',').map(s => s.trim()).filter(Boolean) });
    alert('更新成功');
    renderAgents();
  } catch (e) {
    alert('更新失败: ' + e.message);
  }
}

// ---------- Messages ----------

async function renderMessages() {
  $('#page-title').textContent = 'P2P 交流';
  const main = $('main');
  main.innerHTML = '<div class="empty-state">Loading...</div>';
  try {
    const agents = await api('/admin/agents');
    const agentOptions = agents.map(a => `<option value="${a.agent_id}">${escapeHtml(a.name)} (${a.agent_id})</option>`).join('');
    main.innerHTML = `
      <div class="filter-bar">
        <select id="msg-from"><option value="">From: 全部</option>${agentOptions}</select>
        <select id="msg-to"><option value="">To: 全部</option>${agentOptions}</select>
        <select id="msg-type">
          <option value="">Type: 全部</option>
          <option value="text">text</option>
          <option value="code_review">code_review</option>
          <option value="error">error</option>
          <option value="task">task</option>
          <option value="system">system</option>
          <option value="group">group</option>
        </select>
        <button class="btn primary" onclick="loadMessages()">刷新</button>
      </div>
      <div class="timeline" id="msg-timeline"></div>
    `;
    await loadMessages();
  } catch (e) {
    main.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
  }
}

async function loadMessages() {
  const fromAgent = $('#msg-from')?.value || '';
  const to = $('#msg-to')?.value || '';
  const msgType = $('#msg-type')?.value || '';
  const params = new URLSearchParams();
  if (fromAgent) params.set('from_agent', fromAgent);
  if (to) params.set('to', to);
  if (msgType) params.set('msg_type', msgType);
  try {
    const msgs = await api('/admin/messages?' + params.toString());
    $('#msg-timeline').innerHTML = renderMsgList(msgs);
  } catch (e) {
    $('#msg-timeline').innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
  }
}

function renderMsgList(msgs) {
  if (!msgs || msgs.length === 0) return '<div class="empty-state">暂无消息</div>';
  return msgs.map(m => renderMsgItem(m)).join('');
}

function renderMsgItem(m) {
  const unread = !m.read_at;
  const detail = m.content?.detail ? `<pre class="msg-detail">${escapeHtml(JSON.stringify(m.content.detail, null, 2))}</pre>` : '';
  return `
    <div class="msg-item ${unread ? 'unread' : ''}">
      <div class="msg-header">
        <span class="msg-from">${escapeHtml(m.from_agent)}</span>
        <span style="color:var(--text-muted)">→</span>
        <span class="msg-to">${escapeHtml(m.to)}</span>
        <span class="msg-type">${m.msg_type}</span>
        <span class="msg-time">${formatTime(m.timestamp)} ${unread ? '<span style="color:var(--accent)">● 未读</span>' : ''}</span>
      </div>
      <div class="msg-summary">${escapeHtml(m.content?.summary || '')}</div>
      ${detail}
      ${m.require_human_confirm ? `<div style="margin-top:8px;font-size:0.8rem;color:var(--warning)">⏳ 等待人类确认</div>` : ''}
    </div>
  `;
}

// ---------- Stats ----------

async function renderStats() {
  $('#page-title').textContent = '服务质量';
  const main = $('main');
  main.innerHTML = '<div class="empty-state">Loading...</div>';
  try {
    const stats = await api('/admin/stats');
    main.innerHTML = `
      <div class="card-grid">
        <div class="card"><div class="card-label">总 Agent 数</div><div class="card-value">${stats.total_agents}</div></div>
        <div class="card"><div class="card-label">在线 Agent</div><div class="card-value success">${stats.online_agents}</div></div>
        <div class="card"><div class="card-label">总消息数</div><div class="card-value">${stats.total_messages}</div></div>
        <div class="card"><div class="card-label">未读消息</div><div class="card-value warning">${stats.unread_messages}</div></div>
        <div class="card"><div class="card-label">今日消息</div><div class="card-value">${stats.messages_today}</div></div>
        <div class="card"><div class="card-label">平均读取延迟</div><div class="card-value">${(stats.avg_read_latency_ms || 0).toFixed(0)}ms</div></div>
      </div>
      <div class="section-title">Agent 在线状态</div>
      <div id="agent-status-list"></div>
    `;
    const agents = await api('/admin/agents');
    $('#agent-status-list').innerHTML = `<table><thead><tr><th>Agent</th><th>Status</th><th>Unread</th><th>Last Seen</th></tr></thead><tbody>
      ${agents.map(a => `<tr>
        <td>${escapeHtml(a.name)}</td>
        <td><span class="badge ${a.online ? 'online' : 'offline'}">${a.online ? 'Online' : 'Offline'}</span></td>
        <td>${a.unread_count || 0}</td>
        <td>${timeAgo(a.last_seen)}</td>
      </tr>`).join('')}
    </tbody></table>`;
  } catch (e) {
    main.innerHTML = `<div class="empty-state">Error: ${e.message}</div>`;
  }
}

// ---------- Utils ----------

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------- Init ----------

document.addEventListener('DOMContentLoaded', () => {
  navigate();
});
