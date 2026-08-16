// ═══════════════════════════════════════════════════════════════════
// ADMIN DASHBOARD
// ═══════════════════════════════════════════════════════════════════

function toggleAdminNav(show) {
  const section = document.querySelector('.admin-nav-section');
  const item = document.getElementById('nav-admin');
  if (section) section.style.display = show ? 'block' : 'none';
  if (item) item.style.display = show ? 'flex' : 'none';
}

let adminState = {
  users: { data: [], total: 0, limit: 50, offset: 0 },
  tasks: { data: [], total: 0, limit: 100, offset: 0 },
  invoices: [],
  plans: [],
  stats: null,
};

let adminActiveTab = 'overview';

function initAdminDashboard() {
  // Subtab switching
  document.querySelectorAll('.admin-subtab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.admin-subtab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const target = tab.dataset.adminTab;
      adminActiveTab = target;
      document.querySelectorAll('.admin-tab-content').forEach(c => c.classList.remove('active'));
      const targetEl = document.getElementById('admin-tab-' + target);
      if (targetEl) targetEl.classList.add('active');
      loadAdminTab(target);
    });
  });

  // Toolbar actions
  const userSearch = $('admin-user-search');
  const userStatus = $('admin-user-status');
  const userRefresh = $('admin-user-refresh');
  if (userSearch) userSearch.addEventListener('input', debounce(() => loadAdminUsers(0), 300));
  if (userStatus) userStatus.addEventListener('change', () => loadAdminUsers(0));
  if (userRefresh) userRefresh.addEventListener('click', () => loadAdminUsers(0));

  const taskStatus = $('admin-task-status');
  const taskRefresh = $('admin-task-refresh');
  if (taskStatus) taskStatus.addEventListener('change', () => loadAdminTasks());
  if (taskRefresh) taskRefresh.addEventListener('click', () => loadAdminTasks());

  const invoiceStatus = $('admin-invoice-status');
  const invoiceRefresh = $('admin-invoice-refresh');
  if (invoiceStatus) invoiceStatus.addEventListener('change', () => loadAdminInvoices());
  if (invoiceRefresh) invoiceRefresh.addEventListener('click', () => loadAdminInvoices());

  const logLevel = $('admin-log-level');
  const logRefresh = $('admin-log-refresh');
  if (logLevel) logLevel.addEventListener('change', () => loadAdminLogs());
  if (logRefresh) logRefresh.addEventListener('click', () => loadAdminLogs());

  // Close button
  const closeBtn = $('admin-close-btn');
  if (closeBtn) {
    closeBtn.addEventListener('click', closeAdminDashboard);
  }
}

function openAdminDashboard() {
  const fullscreen = $('admin-fullscreen');
  if (!fullscreen) return;
  fullscreen.style.display = 'block';
  document.body.style.overflow = 'hidden';
  loadAdminTab(adminActiveTab);
}
window.openAdminDashboard = openAdminDashboard;

function closeAdminDashboard() {
  const fullscreen = $('admin-fullscreen');
  if (fullscreen) fullscreen.style.display = 'none';
  document.body.style.overflow = '';
}
window.closeAdminDashboard = closeAdminDashboard;

function loadAdminTab(tab) {
  switch (tab) {
    case 'overview': loadAdminStats(); break;
    case 'users': loadAdminUsers(0); break;
    case 'tasks': loadAdminTasks(); break;
    case 'billing': loadAdminInvoices(); break;
    case 'logs': loadAdminLogs(); break;
    case 'plans': loadAdminPlans(); break;
  }
}

async function loadAdminStats() {
  try {
    const res = await apiFetch('/api/admin/stats');
    if (!res.ok) throw new Error('Failed to load stats');
    const data = await res.json();
    adminState.stats = data;
    $('admin-stat-total-users').textContent = data.users.total;
    $('admin-stat-active-users').textContent = data.users.active;
    $('admin-stat-tasks-today').textContent = data.tasks.today;
    $('admin-stat-tasks-month').textContent = data.tasks.this_month;
    $('admin-stat-revenue').textContent = 'Rp' + Number(data.revenue.total_paid).toLocaleString('id-ID');
    $('admin-stat-storage').textContent = formatBytes(data.storage.total_bytes);
    $('admin-queue-status').innerHTML = `
      <div>Running: ${data.queue.running}</div>
      <div>Queued: ${data.queue.queued}</div>
      <div>Max Workers: ${data.queue.max_workers}</div>
    `;
  } catch (e) {
    console.error('admin stats error', e);
  }
}

async function loadAdminUsers(offset = 0) {
  const search = $('admin-user-search') ? $('admin-user-search').value.trim() : '';
  const status = $('admin-user-status') ? $('admin-user-status').value : '';
  const limit = adminState.users.limit;
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (search) params.set('search', search);
  if (status) params.set('status', status);
  try {
    const res = await apiFetch('/api/admin/users?' + params.toString());
    if (!res.ok) throw new Error('Failed to load users');
    const data = await res.json();
    adminState.users = { ...data, limit, offset };
    renderAdminUsers(data.users);
    renderAdminPagination(data.total, limit, offset, loadAdminUsers);
  } catch (e) {
    console.error('admin users error', e);
  }
}

function renderAdminUsers(users) {
  const tbody = document.querySelector('#admin-users-table tbody');
  if (!tbody) return;
  tbody.innerHTML = users.map(u => `
    <tr>
      <td>${u.id}</td>
      <td>${escapeHtml(u.email)}</td>
      <td>${escapeHtml(u.name || '')}</td>
      <td><span class="admin-status-badge ${u.is_active ? 'active' : 'suspended'}">${u.is_active ? 'Active' : 'Suspended'}</span></td>
      <td>${u.is_admin ? '✅' : ''}</td>
      <td>${u.email_verified ? '✅' : ''}</td>
      <td>${formatDate(u.created_at)}</td>
      <td>
        <button class="btn btn-small" onclick="adminUpdateUser(${u.id}, {is_active: ${u.is_active ? 0 : 1}})">${u.is_active ? 'Suspend' : 'Activate'}</button>
        <button class="btn btn-small" onclick="adminUpdateUser(${u.id}, {is_admin: ${u.is_admin ? 0 : 1}})">${u.is_admin ? 'Revoke Admin' : 'Make Admin'}</button>
      </td>
    </tr>
  `).join('');
}

async function adminUpdateUser(userId, updates) {
  try {
    const res = await apiFetch('/api/admin/users/' + userId, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    });
    if (!res.ok) throw new Error('Update failed');
    loadAdminUsers(adminState.users.offset);
  } catch (e) {
    alert(e.message);
  }
}
window.adminUpdateUser = adminUpdateUser;

async function loadAdminTasks() {
  const status = $('admin-task-status') ? $('admin-task-status').value : '';
  const params = new URLSearchParams({ limit: '100', offset: '0' });
  if (status) params.set('status', status);
  try {
    const res = await apiFetch('/api/admin/tasks?' + params.toString());
    if (!res.ok) throw new Error('Failed to load tasks');
    const data = await res.json();
    const tbody = document.querySelector('#admin-tasks-table tbody');
    if (!tbody) return;
    tbody.innerHTML = data.tasks.map(t => `
      <tr>
        <td>${t.id}</td>
        <td>${t.user_id || '-'}</td>
        <td>${t.status}</td>
        <td>${t.progress}%</td>
        <td>${escapeHtml(t.error || '')}</td>
        <td>${formatDate(t.created_at)}</td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('admin tasks error', e);
  }
}

async function loadAdminInvoices() {
  const status = $('admin-invoice-status') ? $('admin-invoice-status').value : '';
  const params = new URLSearchParams({ limit: '100', offset: '0' });
  if (status) params.set('status', status);
  try {
    const res = await apiFetch('/api/admin/invoices?' + params.toString());
    if (!res.ok) throw new Error('Failed to load invoices');
    const data = await res.json();
    const tbody = document.querySelector('#admin-invoices-table tbody');
    if (!tbody) return;
    tbody.innerHTML = data.invoices.map(inv => `
      <tr>
        <td>${inv.id}</td>
        <td>${inv.user_id}</td>
        <td>${inv.plan_code}</td>
        <td>Rp${Number(inv.amount).toLocaleString('id-ID')}</td>
        <td>${inv.status}</td>
        <td>${formatDate(inv.created_at)}</td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('admin invoices error', e);
  }
}

async function loadAdminLogs() {
  const level = $('admin-log-level') ? $('admin-log-level').value : '';
  const params = new URLSearchParams({ lines: '200' });
  if (level) params.set('level', level);
  try {
    const res = await apiFetch('/api/admin/logs?' + params.toString());
    if (!res.ok) throw new Error('Failed to load logs');
    const data = await res.json();
    const viewer = $('admin-log-viewer');
    if (viewer) viewer.textContent = (data.logs || []).join('\n');
  } catch (e) {
    console.error('admin logs error', e);
  }
}

async function loadAdminPlans() {
  try {
    const res = await apiFetch('/api/admin/plans');
    if (!res.ok) throw new Error('Failed to load plans');
    const data = await res.json();
    adminState.plans = data.plans;
    const grid = $('admin-plans-grid');
    if (!grid) return;
    grid.innerHTML = data.plans.map(plan => `
      <div class="admin-plan-card" data-plan-code="${plan.code}">
        <p class="card-title">${plan.name} (${plan.code})</p>
        <label>Price</label>
        <input type="number" class="admin-plan-price" value="${plan.price}">
        <label>Currency</label>
        <input type="text" class="admin-plan-currency" value="${plan.currency}">
        <label>Trial Days</label>
        <input type="number" class="admin-plan-trial" value="${plan.trial_days}">
        ${Object.keys(plan.limits).map(key => `
          <label>${key}</label>
          <input type="number" class="admin-plan-limit" data-limit="${key}" value="${plan.limits[key]}">
        `).join('')}
        <div class="admin-plan-actions">
          <button class="btn btn-primary btn-small" onclick="adminSavePlan('${plan.code}')">Save</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    console.error('admin plans error', e);
  }
}

async function adminSavePlan(planCode) {
  const card = document.querySelector(`.admin-plan-card[data-plan-code="${planCode}"]`);
  if (!card) return;
  const updates = {
    price: Number(card.querySelector('.admin-plan-price').value),
    currency: card.querySelector('.admin-plan-currency').value,
    trial_days: Number(card.querySelector('.admin-plan-trial').value),
    limits: {},
  };
  card.querySelectorAll('.admin-plan-limit').forEach(input => {
    updates.limits[input.dataset.limit] = Number(input.value);
  });
  try {
    const res = await apiFetch('/api/admin/plans/' + planCode, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    });
    if (!res.ok) throw new Error('Save failed');
    alert('Plan updated');
    loadAdminPlans();
  } catch (e) {
    alert(e.message);
  }
}
window.adminSavePlan = adminSavePlan;

function renderAdminPagination(total, limit, offset, callback) {
  const container = $('admin-users-pagination');
  if (!container) return;
  const pages = Math.ceil(total / limit);
  const current = Math.floor(offset / limit);
  if (pages <= 1) {
    container.innerHTML = '';
    return;
  }
  let html = '';
  for (let i = 0; i < pages; i++) {
    html += `<button class="${i === current ? 'active' : ''}" onclick="adminGoToPage(${i})">${i + 1}</button>`;
  }
  container.innerHTML = html;
}
window.adminGoToPage = function(page) {
  loadAdminUsers(page * adminState.users.limit);
};

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatDate(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleString('id-ID');
}

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let size = bytes;
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024;
    i++;
  }
  return size.toFixed(2) + ' ' + units[i];
}

function debounce(fn, wait) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

// Initialize admin dashboard after DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  initAdminDashboard();
  if (typeof currentUser !== 'undefined' && currentUser && typeof toggleAdminNav === 'function') {
    toggleAdminNav(currentUser.is_admin === true);
  }
});
