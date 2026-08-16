/* app.js — Video Clipper Dashboard — Frontend Logic */

const $ = id => document.getElementById(id);

// ── State ────────────────────────────────────────────────────────────────
let currentTaskId = null;
let evtSource     = null;
let currentUser   = null;

// ── DOM refs ─────────────────────────────────────────────────────────────
const form            = $('clip-form');
const subtitleToggle  = $('subtitle-toggle');
const subtitleOptions = $('subtitle-options');
const subtitleLang    = $('subtitle-lang');
const subtitleType    = $('subtitle-type');
const subtitleAuto    = $('subtitle-auto');
const positionRow     = $('position-row');
const styleRow        = $('style-row');
const subFontsize     = $('sub-fontsize');
const subCase         = $('sub-case');
const subtitleSource  = $('subtitle-source');
const whisperModel    = $('whisper-model');
const whisperModelField = $('whisper-model-field');


const subBold         = $('sub-bold');
const subItalic       = $('sub-italic');
const subUnderline    = $('sub-underline');
const videoFormat     = $('video-format');
const downloadResolution = $('download-resolution');
const outputResolution   = $('output-resolution');
const outputQuality      = $('output-quality');
const urlInput        = $('url-input');
const startInput      = $('start-input');
const endInput        = $('end-input');
const btnClip         = $('btn-clip');
const progressSection = $('progress-section');
const progressFill    = $('progress-fill');
const progressPct     = $('progress-pct');
const statusLabel     = $('status-label');
const statusPulse     = $('status-pulse');
const terminalBody    = $('terminal-body');
const downloadCard    = $('download-card');
const downloadLink    = $('download-link');
const downloadName    = $('download-name');
const previewVideo    = $('preview-video');
const errorCard       = $('error-card');
const errorMsg        = $('error-msg');

// Panel-specific duplicates
const progressFillPanel  = $('progress-fill-panel');
const progressPctPanel   = $('progress-pct-panel');
const statusLabelPanel   = $('status-label-panel');
const statusPulsePanel   = $('status-pulse-panel');
const downloadLinkPanel  = $('download-link-panel');

// Workspace state elements
const wsEmpty              = $('ws-empty');
const wsInfo               = $('ws-info');
const wsProcessing         = $('ws-processing');
const wsPlayer             = $('ws-player');
const wsGallery            = $('ws-gallery');
const workspaceVideoPlayer = $('workspace-video-player');
const timelineInfo         = $('timeline-info');
const timelineClipRegion   = $('timeline-clip-region');
const timelineClipLabel    = $('timeline-clip-label');
const timelineProgressSection = $('timeline-progress-section');
const timelineTrack        = $('timeline-track');
const timelineHandleStart  = $('timeline-handle-start');
const timelineHandleEnd    = $('timeline-handle-end');

// Workspace info cache
let cachedVideoInfo = null;
let videoInfoFetchController = null;

// Hidden style preset inputs
const subPrimaryColor = $('sub-primary-color');
const subOutlineColor = $('sub-outline-color');
const subBackColor    = $('sub-back-color');
const subBackAlpha    = $('sub-back-alpha');
const subBorderStyle  = $('sub-border-style');
const subOutlineWidth = $('sub-outline-width');
const subShadowVal    = $('sub-shadow-val');

// ── Auth refs
const authOverlay     = $('auth-overlay');
const authForm        = $('auth-form');
const authSubtitle    = $('auth-subtitle');
const authNameGroup   = $('auth-name-group');
const authPassword2Group = $('auth-password2-group');
const authEmailGroup  = $('auth-email-group');
const authPasswordGroup = $('auth-password-group');
const authResetPasswordGroup = $('auth-reset-password-group');
const authResetPassword2Group = $('auth-reset-password2-group');
const authName        = $('auth-name');
const authEmail       = $('auth-email');
const authPassword    = $('auth-password');
const authPassword2   = $('auth-password2');
const authResetPassword = $('auth-reset-password');
const authResetPassword2 = $('auth-reset-password2');
const authToken       = $('auth-token');
const authSubmit      = $('auth-submit');
const authError       = $('auth-error');
const authMessage     = $('auth-message');
const authModeText    = $('auth-mode-text');
const authToggle      = $('auth-toggle');
const authForgot      = $('auth-forgot');
const userBanner      = $('user-banner');
const userNameEl      = $('user-name');
const btnLogout       = $('btn-logout');
const quotaBadge      = $('quota-badge');
const btnUpgrade      = $('btn-upgrade');
const socialLogin     = $('social-login');
const socialLoginButtons = $('social-login-buttons');
let authMode = 'login'; // 'login' | 'register' | 'forgot' | 'reset' | 'verify'

// ═══════════════════════════════════════════════════════════════════
// AUTHENTICATION
// ═══════════════════════════════════════════════════════════════════

async function apiFetch(url, options = {}, retry = true) {
  options.credentials = 'same-origin';
  options.headers = options.headers || {};
  if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
    options.headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(url, options);
  if (res.status === 401 && retry) {
    // Try to refresh the access token using the httpOnly refresh cookie.
    try {
      const refreshRes = await fetch('/api/auth/refresh', {
        method: 'POST',
        credentials: 'same-origin',
      });
      if (refreshRes.ok) {
        return apiFetch(url, options, false);
      }
    } catch (e) {
      // ignore, show overlay below
    }
    showAuthOverlay();
    throw new Error('Sesi habis. Silakan masuk kembali.');
  }
  return res;
}

function showAuthOverlay() {
  if (authOverlay) authOverlay.classList.remove('hidden');
}

function hideAuthOverlay() {
  if (authOverlay) authOverlay.classList.add('hidden');
}

function updateQuota(usage) {
  if (!usage || !quotaBadge) return;
  const quota = usage.metrics && usage.metrics.clip_count;
  if (!quota) return;
  quotaBadge.textContent = `${usage.plan.toUpperCase()} ${quota.used}/${quota.limit}`;
  quotaBadge.classList.toggle('warning', quota.remaining > 0 && quota.remaining <= Math.max(1, quota.limit * 0.2));
  quotaBadge.classList.toggle('exhausted', quota.remaining <= 0);
  if (quota.remaining <= Math.max(1, quota.limit * 0.2) && btnUpgrade) btnUpgrade.style.display = 'inline-flex';
}

function updateUserBanner(user, usage) {
  if (!userBanner || !userNameEl) return;
  userNameEl.textContent = user.name || user.email;
  userBanner.style.display = 'flex';
  updateQuota(usage);
}

async function loadSocialProviders() {
  try {
    const res = await fetch('/api/auth/social/providers');
    const data = await res.json();
    if (!data.providers || !data.providers.length || !socialLoginButtons) return;
    socialLoginButtons.innerHTML = data.providers.map(provider =>
      `<a class="btn btn-secondary social-btn" href="/api/auth/social/${provider}">MASUK DENGAN ${provider.toUpperCase()}</a>`
    ).join('');
    if (socialLogin) socialLogin.style.display = 'block';
  } catch (e) { /* optional integration */ }
}

function setAuthMode(mode, message = '') {
  authMode = mode;
  authError.style.display = 'none';
  authMessage.style.display = 'none';

  // Reset fields
  if (authPassword) authPassword.value = '';
  if (authPassword2) authPassword2.value = '';
  if (authResetPassword) authResetPassword.value = '';
  if (authResetPassword2) authResetPassword2.value = '';

  // Default visibility
  if (authNameGroup) authNameGroup.style.display = 'none';
  if (authPassword2Group) authPassword2Group.style.display = 'none';
  if (authEmailGroup) authEmailGroup.style.display = 'block';
  if (authPasswordGroup) authPasswordGroup.style.display = 'block';
  if (authResetPasswordGroup) authResetPasswordGroup.style.display = 'none';
  if (authResetPassword2Group) authResetPassword2Group.style.display = 'none';
  if (authForgot) authForgot.style.display = 'none';

  if (mode === 'register') {
    if (authNameGroup) authNameGroup.style.display = 'block';
    if (authPassword2Group) authPassword2Group.style.display = 'block';
    if (authSubmit) authSubmit.textContent = 'DAFTAR';
    if (authModeText) authModeText.textContent = 'Sudah punya akun?';
    if (authToggle) authToggle.textContent = 'Masuk';
    if (authSubtitle) authSubtitle.textContent = 'Buat akun gratis untuk mulai membuat klip.';
  } else if (mode === 'forgot') {
    if (authPasswordGroup) authPasswordGroup.style.display = 'none';
    if (authSubmit) authSubmit.textContent = 'KIRIM LINK RESET';
    if (authModeText) authModeText.textContent = 'Kembali ke';
    if (authToggle) authToggle.textContent = 'Masuk';
    if (authSubtitle) authSubtitle.textContent = 'Masukkan email Anda untuk menerima link reset password.';
  } else if (mode === 'reset') {
    if (authEmailGroup) authEmailGroup.style.display = 'none';
    if (authPasswordGroup) authPasswordGroup.style.display = 'none';
    if (authResetPasswordGroup) authResetPasswordGroup.style.display = 'block';
    if (authResetPassword2Group) authResetPassword2Group.style.display = 'block';
    if (authSubmit) authSubmit.textContent = 'UBAH PASSWORD';
    if (authModeText) authModeText.textContent = 'Kembali ke';
    if (authToggle) authToggle.textContent = 'Masuk';
    if (authSubtitle) authSubtitle.textContent = 'Masukkan password baru Anda.';
  } else if (mode === 'verify') {
    if (authEmailGroup) authEmailGroup.style.display = 'block';
    if (authPasswordGroup) authPasswordGroup.style.display = 'none';
    if (authSubmit) authSubmit.textContent = 'KIRIM ULANG EMAIL';
    if (authModeText) authModeText.textContent = 'Sudah punya akun?';
    if (authToggle) authToggle.textContent = 'Masuk';
    if (authSubtitle) authSubtitle.textContent = 'Verifikasi email Anda untuk melanjutkan.';
  } else {
    // login
    if (authForgot) authForgot.style.display = 'inline-block';
    if (authSubmit) authSubmit.textContent = 'MASUK';
    if (authModeText) authModeText.textContent = 'Belum punya akun?';
    if (authToggle) authToggle.textContent = 'Daftar';
    if (authSubtitle) authSubtitle.textContent = 'Masuk atau daftar untuk mulai membuat klip.';
  }

  if (message && authMessage) {
    authMessage.textContent = message;
    authMessage.style.display = 'block';
  }
}

async function checkAuth() {
  try {
    const res = await apiFetch('/api/auth/me');
    const data = await res.json();
    if (data.authenticated && data.user) {
      currentUser = data.user;
      hideAuthOverlay();
      updateUserBanner(data.user, data.usage);
      if (typeof toggleAdminNav === 'function') {
        toggleAdminNav(data.user.is_admin === true);
      }
      return true;
    }
  } catch (e) {
    // ignore, show overlay below
  }
  currentUser = null;
  toggleAdminNav(false);
  showAuthOverlay();
  return false;
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  if (authError) authError.style.display = 'none';
  if (authMessage) authMessage.style.display = 'none';

  const email = authEmail ? authEmail.value.trim() : '';
  const password = authPassword ? authPassword.value : '';
  const payload = {};
  let url = '/api/auth/login';

  if (authMode === 'register') {
    const name = authName ? authName.value.trim() : '';
    const password2 = authPassword2 ? authPassword2.value : '';
    if (!name) {
      if (authError) { authError.textContent = 'Nama wajib diisi.'; authError.style.display = 'block'; }
      return;
    }
    if (password !== password2) {
      if (authError) { authError.textContent = 'Password dan konfirmasi tidak cocok.'; authError.style.display = 'block'; }
      return;
    }
    payload.email = email;
    payload.password = password;
    payload.name = name;
    url = '/api/auth/register';
  } else if (authMode === 'forgot') {
    if (!email) {
      if (authError) { authError.textContent = 'Email wajib diisi.'; authError.style.display = 'block'; }
      return;
    }
    payload.email = email;
    url = '/api/auth/forgot-password';
  } else if (authMode === 'reset') {
    const newPassword = authResetPassword ? authResetPassword.value : '';
    const newPassword2 = authResetPassword2 ? authResetPassword2.value : '';
    const token = authToken ? authToken.value : '';
    if (!token) {
      if (authError) { authError.textContent = 'Token reset tidak valid.'; authError.style.display = 'block'; }
      return;
    }
    if (newPassword !== newPassword2) {
      if (authError) { authError.textContent = 'Password baru dan konfirmasi tidak cocok.'; authError.style.display = 'block'; }
      return;
    }
    payload.token = token;
    payload.password = newPassword;
    url = '/api/auth/reset-password';
  } else if (authMode === 'verify') {
    if (!email) {
      if (authError) { authError.textContent = 'Email wajib diisi.'; authError.style.display = 'block'; }
      return;
    }
    payload.email = email;
    url = '/api/auth/resend-verification';
  } else {
    // login
    payload.email = email;
    payload.password = password;
    url = '/api/auth/login';
  }

  try {
    const res = await apiFetch(url, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      if (authError) { authError.textContent = data.error || 'Terjadi kesalahan.'; authError.style.display = 'block'; }
      return;
    }

    if (authMode === 'register') {
      if (data.email_verification_required && !data.user.email_verified) {
        setAuthMode('verify', 'Pendaftaran berhasil! Silakan verifikasi email Anda sebelum masuk.');
        return;
      }
      hideAuthOverlay();
      updateUserBanner(data.user);
    } else if (authMode === 'forgot' || authMode === 'reset' || authMode === 'verify') {
      if (authMessage) { authMessage.textContent = data.message || 'Berhasil.'; authMessage.style.display = 'block'; }
      if (authMode === 'reset') {
        setTimeout(() => setAuthMode('login'), 2000);
      }
    } else {
      hideAuthOverlay();
      updateUserBanner(data.user);
    }
  } catch (e) {
    if (authError) { authError.textContent = e.message || 'Terjadi kesalahan.'; authError.style.display = 'block'; }
  }
}

async function handleLogout() {
  try {
    await apiFetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {
    // ignore
  }
  window.location.reload();
}

async function handleEmailVerificationToken(token) {
  try {
    const res = await apiFetch('/api/auth/verify-email', {
      method: 'POST',
      body: JSON.stringify({ token }),
    });
    const data = await res.json();
    if (res.ok) {
      setAuthMode('login', 'Email berhasil diverifikasi. Silakan masuk.');
    } else {
      setAuthMode('login', data.error || 'Token verifikasi tidak valid.');
    }
  } catch (e) {
    setAuthMode('login', 'Gagal memverifikasi email.');
  }
}

if (authForm) authForm.addEventListener('submit', handleAuthSubmit);
if (authToggle) authToggle.addEventListener('click', () => {
  if (authMode === 'login') setAuthMode('register');
  else setAuthMode('login');
});
if (authForgot) authForgot.addEventListener('click', () => setAuthMode('forgot'));
if (btnLogout) btnLogout.addEventListener('click', handleLogout);

// Handle verify-email / reset-password links on page load.
window.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  const verifyToken = params.get('token');
  const authErrorParam = params.get('auth_error');
  if (params.get('auth_status') === 'success') {
    window.history.replaceState({}, document.title, '/');
    checkAuth();
  } else if (authErrorParam) {
    setAuthMode('login');
    if (authError) { authError.textContent = authErrorParam; authError.style.display = 'block'; }
    window.history.replaceState({}, document.title, '/');
  } else if (window.location.pathname === '/verify-email' && verifyToken) {
    showAuthOverlay();
    handleEmailVerificationToken(verifyToken);
    // clean URL
    window.history.replaceState({}, document.title, '/');
  } else if (window.location.pathname === '/reset-password' && verifyToken) {
    showAuthOverlay();
    if (authToken) authToken.value = verifyToken;
    setAuthMode('reset');
    window.history.replaceState({}, document.title, '/');
  }
  loadSocialProviders();
  checkAuth();
});

// ═══════════════════════════════════════════════════════════════════
// PROFILE MODAL
// ═══════════════════════════════════════════════════════════════════

const profileModal      = $('profile-modal');
const profileModalClose = $('profile-modal-close');
const btnProfile        = $('btn-profile');
const profileForm       = $('profile-form');
const profileName       = $('profile-name');
const profileAvatar     = $('profile-avatar');
const profileTimezone   = $('profile-timezone');
const profileLanguage   = $('profile-language');
const profileError      = $('profile-error');
const profileMessage    = $('profile-message');
const billingCurrentPlan = $('billing-current-plan');
const billingStatus = $('billing-status');
const pricingPlans = $('pricing-plans');
const invoiceList = $('invoice-list');
const btnSubCancel = $('btn-sub-cancel');
const btnSubPause = $('btn-sub-pause');
const btnSubResume = $('btn-sub-resume');

function openProfileModal() {
  if (!profileModal) return;
  profileModal.style.display = 'flex';
  loadProfile();
  loadBilling();
}

async function loadBilling() {
  try {
    const [plansRes, subRes] = await Promise.all([apiFetch('/api/plans'), apiFetch('/api/subscription')]);
    const plansData = await plansRes.json();
    const data = await subRes.json();
    const sub = data.subscription || {};
    if (billingCurrentPlan) billingCurrentPlan.textContent = (sub.plan_code || 'free').toUpperCase();
    if (billingStatus) billingStatus.textContent = (sub.status || 'active').toUpperCase();
    updateQuota(data.usage);
    if (pricingPlans) {
      pricingPlans.innerHTML = (plansData.plans || []).filter(plan => plan.code !== 'free').map(plan => `
        <div class="pricing-plan">
          <strong>${plan.name}</strong><span>Rp${Number(plan.price).toLocaleString('id-ID')}/bulan</span>
          <button type="button" class="btn btn-primary btn-small plan-checkout" data-plan="${plan.code}" ${plansData.billing_configured ? '' : 'disabled'}>PILIH</button>
        </div>`).join('');
      pricingPlans.querySelectorAll('.plan-checkout').forEach(button => button.addEventListener('click', () => startCheckout(button.dataset.plan)));
    }
    if (invoiceList) invoiceList.innerHTML = (data.invoices || []).map(invoice =>
      `<div class="invoice-row"><span>${invoice.id}</span><span>${invoice.status.toUpperCase()}</span></div>`
    ).join('');
  } catch (e) { /* shown only when modal is used */ }
}

async function startCheckout(planCode) {
  const res = await apiFetch('/api/billing/checkout', { method: 'POST', body: JSON.stringify({ plan_code: planCode }) });
  const data = await res.json();
  if (!res.ok) return alert(data.error || 'Checkout gagal.');
  window.location.assign(data.checkout_url);
}

async function subscriptionAction(action) {
  const res = await apiFetch('/api/billing/subscription', { method: 'POST', body: JSON.stringify({ action }) });
  const data = await res.json();
  if (!res.ok) return alert(data.error || 'Perubahan subscription gagal.');
  loadBilling();
}

function closeProfileModal() {
  if (!profileModal) return;
  profileModal.style.display = 'none';
}

async function loadProfile() {
  try {
    const res = await apiFetch('/api/auth/profile');
    const data = await res.json();
    if (res.ok && data.user) {
      if (profileName) profileName.value = data.user.name || '';
      if (profileAvatar) profileAvatar.value = data.user.avatar_url || '';
      if (profileTimezone) profileTimezone.value = data.user.timezone || 'UTC';
      if (profileLanguage) profileLanguage.value = data.user.language || 'id';
    }
  } catch (e) {
    // ignore
  }
}

async function handleProfileSubmit(e) {
  e.preventDefault();
  if (profileError) profileError.style.display = 'none';
  if (profileMessage) profileMessage.style.display = 'none';

  const payload = {
    name: profileName ? profileName.value.trim() : '',
    avatar_url: profileAvatar ? profileAvatar.value.trim() : '',
    timezone: profileTimezone ? profileTimezone.value.trim() : '',
    language: profileLanguage ? profileLanguage.value : 'id',
  };

  try {
    const res = await apiFetch('/api/auth/profile', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      if (profileError) { profileError.textContent = data.error || 'Gagal menyimpan profil.'; profileError.style.display = 'block'; }
      return;
    }
    if (profileMessage) { profileMessage.textContent = 'Profil berhasil disimpan.'; profileMessage.style.display = 'block'; }
    if (data.user) {
      updateUserBanner(data.user);
    }
  } catch (e) {
    if (profileError) { profileError.textContent = e.message || 'Gagal menyimpan profil.'; profileError.style.display = 'block'; }
  }
}

if (btnProfile) btnProfile.addEventListener('click', openProfileModal);
if (profileModalClose) profileModalClose.addEventListener('click', closeProfileModal);
if (profileForm) profileForm.addEventListener('submit', handleProfileSubmit);
if (btnUpgrade) btnUpgrade.addEventListener('click', openProfileModal);
if (quotaBadge) quotaBadge.addEventListener('click', openProfileModal);
if (btnSubCancel) btnSubCancel.addEventListener('click', () => subscriptionAction('cancel'));
if (btnSubPause) btnSubPause.addEventListener('click', () => subscriptionAction('pause'));
if (btnSubResume) btnSubResume.addEventListener('click', () => subscriptionAction('resume'));
if (profileModal) {
  profileModal.addEventListener('click', (e) => {
    if (e.target === profileModal) closeProfileModal();
  });
}

// ═══════════════════════════════════════════════════════════════════
// DASHBOARD NAVIGATION — Sidebar + Panel Tabs + Timeline
// ═══════════════════════════════════════════════════════════════════

// Auto-clip Whisper controls
const ctrlSubtitleSource  = $('ctrl-subtitle-source');
const ctrlWhisperModel    = $('ctrl-whisper-model');
const ctrlWhisperModelField = $('ctrl-whisper-model-field');

function syncSubtitleSource() {
  if (subtitleSource && whisperModelField) {
    whisperModelField.style.display = subtitleSource.value === 'whisper' ? 'block' : 'none';
  }
  if (ctrlSubtitleSource && ctrlWhisperModelField) {
    ctrlWhisperModelField.style.display = ctrlSubtitleSource.value === 'whisper' ? 'block' : 'none';
  }
}

if (subtitleSource) subtitleSource.addEventListener('change', syncSubtitleSource);
if (ctrlSubtitleSource) ctrlSubtitleSource.addEventListener('change', syncSubtitleSource);

// ── Sidebar Navigation ───────────────────────────────────────────
document.querySelectorAll('.nav-item[data-target]').forEach(item => {
  item.addEventListener('click', () => {
    const targetId = item.dataset.target;

    // Admin dashboard opens as a fullscreen overlay
    if (targetId === 'admin-fullscreen') {
      openAdminDashboard();
      return;
    }

    // Close admin dashboard if open
    if (typeof closeAdminDashboard === 'function') {
      closeAdminDashboard();
    }

    // Update active nav item
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    item.classList.add('active');

    // Show corresponding tab-page in panel-controls
    document.querySelectorAll('#panel-controls > .tab-page').forEach(page => {
      page.classList.toggle('active', page.id === targetId);
    });

    // Switch to Controls tab in right panel
    switchPanelTab('panel-controls');
  });
});

// ── Panel Tabs (Controls / Results / AI Copy) ─────────────────────
document.querySelectorAll('.panel-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    switchPanelTab(tab.dataset.panel);
  });
});

function switchPanelTab(panelId) {
  document.querySelectorAll('.panel-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.panel === panelId);
  });
  document.querySelectorAll('.panel-content > .tab-page').forEach(p => {
    p.classList.toggle('active', p.id === panelId);
  });
}

// ── Generate fake timeline waveform ─────────────────────────────
function generateWaveform() {
  const container = $('timeline-waveform');
  if (!container) return;
  container.innerHTML = '';
  const barCount = 120;
  for (let i = 0; i < barCount; i++) {
    const bar = document.createElement('div');
    bar.className = 'timeline-bar';
    const h = Math.random() * 80 + 20;
    bar.style.height = h + '%';
    container.appendChild(bar);
  }
}

// ── Interactive Timeline ────────────────────────────────────────────────
let timelineDuration = 0; // in seconds
let timelineStartSec = 0;
let timelineEndSec   = 0;
let isDragging = null; // 'start' | 'end' | null

function secondsToHMS(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

function setTimelineDuration(seconds) {
  timelineDuration = Math.max(seconds || 0, 1);
}

function updateTimelineUI() {
  if (!timelineClipRegion || timelineDuration <= 0) return;

  const startPct = (timelineStartSec / timelineDuration) * 100;
  const widthPct = ((timelineEndSec - timelineStartSec) / timelineDuration) * 100;

  timelineClipRegion.style.left = Math.max(0, startPct) + '%';
  timelineClipRegion.style.width = Math.max(0, widthPct) + '%';
  timelineClipRegion.classList.add('active');

  const startStr = secondsToHMS(timelineStartSec);
  const endStr = secondsToHMS(timelineEndSec);

  if (timelineClipLabel) timelineClipLabel.textContent = `${startStr} → ${endStr}`;
  if (timelineInfo) timelineInfo.textContent = `${startStr} — ${endStr}`;

  // Also sync time inputs if they exist and user isn't currently editing them
  if (startInput && document.activeElement !== startInput) startInput.value = startStr;
  if (endInput && document.activeElement !== endInput) endInput.value = endStr;
}

function highlightTimeline(startStr, endStr, totalDuration) {
  timelineStartSec = parseTimeToSeconds(startStr);
  timelineEndSec = parseTimeToSeconds(endStr);
  setTimelineDuration(totalDuration || Math.max(timelineEndSec * 1.2, 600));
  updateTimelineUI();
}

function clearTimelineHighlight() {
  if (timelineClipRegion) timelineClipRegion.classList.remove('active');
  if (timelineInfo) timelineInfo.textContent = '00:00:00 — 00:00:00';
  timelineStartSec = 0;
  timelineEndSec = 0;
  timelineDuration = 0;
}

function timelineXToSeconds(clientX) {
  if (!timelineTrack) return 0;
  const rect = timelineTrack.getBoundingClientRect();
  const pct = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
  return pct * timelineDuration;
}

function handleTimelineMouseDown(e) {
  if (!timelineTrack || timelineDuration <= 0) return;
  e.preventDefault();

  const target = e.target;
  if (target.classList.contains('timeline-handle')) {
    isDragging = target.dataset.handle;
    target.classList.add('dragging');
    return;
  }

  // Click on track: move nearest handle
  const clickSec = timelineXToSeconds(e.clientX);
  const distStart = Math.abs(clickSec - timelineStartSec);
  const distEnd = Math.abs(clickSec - timelineEndSec);
  isDragging = distStart <= distEnd ? 'start' : 'end';
  updateTimelineFromMouse(e.clientX);
}

function updateTimelineFromMouse(clientX) {
  if (!isDragging || timelineDuration <= 0) return;
  let sec = timelineXToSeconds(clientX);

  if (isDragging === 'start') {
    timelineStartSec = Math.min(sec, timelineEndSec - 1);
  } else {
    timelineEndSec = Math.max(sec, timelineStartSec + 1);
  }
  updateTimelineUI();
}

function stopTimelineDrag() {
  if (isDragging) {
    const handle = isDragging === 'start' ? timelineHandleStart : timelineHandleEnd;
    if (handle) handle.classList.remove('dragging');
    isDragging = null;
  }
}

if (timelineTrack) {
  timelineTrack.addEventListener('mousedown', handleTimelineMouseDown);
  window.addEventListener('mousemove', (e) => {
    if (isDragging) updateTimelineFromMouse(e.clientX);
  });
  window.addEventListener('mouseup', stopTimelineDrag);
  // Touch support
  timelineTrack.addEventListener('touchstart', (e) => handleTimelineMouseDown(e.touches[0]));
  window.addEventListener('touchmove', (e) => {
    if (isDragging && e.touches[0]) updateTimelineFromMouse(e.touches[0].clientX);
  });
  window.addEventListener('touchend', stopTimelineDrag);
}

function parseTimeToSeconds(timeStr) {
  if (!timeStr) return 0;
  timeStr = timeStr.trim();
  if (/^\d+(\.\d+)?$/.test(timeStr)) return parseFloat(timeStr);
  const parts = timeStr.split(':').map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return 0;
}

// ── Dependency Check ─────────────────────────────────────────────────────
async function checkDeps() {
  try {
    const res  = await fetch('/check-deps');
    const data = await res.json();

    const ytBadge  = $('dep-ytdlp');
    const ffBadge  = $('dep-ffmpeg');

    if (data.yt_dlp) {
      ytBadge.classList.add('ok');
      ytBadge.querySelector('.dep-label').textContent = `yt-dlp ${data.yt_dlp}`;
    } else {
      ytBadge.classList.add('error');
      ytBadge.querySelector('.dep-label').textContent = 'yt-dlp: tidak ditemukan';
    }

    if (data.ffmpeg) {
      ffBadge.classList.add('ok');
      ffBadge.querySelector('.dep-label').textContent = 'FFmpeg ✓';
    } else {
      ffBadge.classList.add('error');
      ffBadge.querySelector('.dep-label').textContent = 'FFmpeg: tidak ditemukan';
    }
  } catch (e) {
    console.warn('Dep check failed:', e);
  }
}

// ── Clip Button Click ─────────────────────────────────────────────────────
btnClip.addEventListener('click', async () => {

  const url   = urlInput.value.trim();
  const start = startInput.value.trim();
  const end   = endInput.value.trim();

  if (!url)   { alert('URL video wajib diisi!');        urlInput.focus();   return; }
  if (!start) { alert('Waktu mulai wajib diisi!');      startInput.focus(); return; }
  if (!end)   { alert('Waktu selesai wajib diisi!');    endInput.focus();   return; }

  // Reset UI
  resetUI();
  setLoading(true);
  progressSection.classList.add('visible');
  downloadCard.classList.remove('visible');
  errorCard.classList.remove('visible');
  terminalBody.innerHTML = '';

  // Switch to Results tab
  switchPanelTab('panel-results');

  // Show progress on timeline
  showTimelineProgress(true);
  highlightTimeline(start, end);

  // Switch workspace to processing state
  showWorkspaceProcessing();

  try {
    const subtitlePosition = document.querySelector('input[name="subtitle-position"]:checked')?.value || 'bottom';
    const hookTitleValue = $('hook-title') ? $('hook-title').value.trim() : '';
    const hookFontsize = $('hook-fontsize') ? $('hook-fontsize').value : "34";
    const hookPreset = $('hook-style') ? $('hook-style').value : "yellow-pop";
    const hookPosition = $('hook-position') ? $('hook-position').value : "top";
    
    let finalSubType = subtitleType ? subtitleType.value : 'soft';

    const res  = await apiFetch('/clip', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        url, start, end,
        hook_title:        hookTitleValue,
        hook_fontsize:     hookFontsize,
        hook_preset:       hookPreset,
        hook_position:     hookPosition,

        subtitle_enabled:  subtitleToggle ? subtitleToggle.checked : false,
        subtitle_lang:     subtitleLang   ? subtitleLang.value     : 'id,en',
        subtitle_type:     finalSubType,
        subtitle_auto:     subtitleAuto   ? subtitleAuto.checked   : true,
        subtitle_position: subtitlePosition,
        sub_fontsize:      subFontsize    ? subFontsize.value      : '20',
        sub_case:          subCase        ? subCase.value          : 'normal',
        sub_bold:          subBold        ? subBold.checked        : false,
        sub_italic:        subItalic      ? subItalic.checked      : false,
        sub_underline:     subUnderline   ? subUnderline.checked   : false,
        subtitle_style:    $('subtitle-style') ? $('subtitle-style').value : 'standard',
        bgm_type:          $('bgm-type')  ? $('bgm-type').value    : 'none',
        auto_broll:        $('broll-toggle') ? $('broll-toggle').checked : false,
        video_format:      videoFormat    ? videoFormat.value      : 'original',
        download_resolution: downloadResolution ? downloadResolution.value : 'best',
        output_resolution:   outputResolution   ? outputResolution.value   : '1080',
        output_quality:      outputQuality      ? outputQuality.value      : 'standard',
        cookies:           $('manual-cookies') ? $('manual-cookies').value : '',
        transcription_source: subtitleSource ? subtitleSource.value : 'auto',
        whisper_model:     whisperModel     ? whisperModel.value     : 'base',
        // Preset style params
        sub_primary_color: subPrimaryColor ? subPrimaryColor.value : 'FFFFFF',
        sub_outline_color: subOutlineColor ? subOutlineColor.value : '000000',
        sub_back_color:    subBackColor    ? subBackColor.value    : '000000',
        sub_back_alpha:    subBackAlpha    ? subBackAlpha.value    : '00',
        sub_border_style:  subBorderStyle  ? subBorderStyle.value  : '1',
        sub_outline_width: subOutlineWidth ? subOutlineWidth.value : '2',
        sub_shadow:        subShadowVal    ? subShadowVal.value    : '1',
      }),
    });
    const data = await res.json();

    if (data.error) {
      if (data.usage) updateQuota(data.usage);
      showError(data.error);
      setLoading(false);
      showTimelineProgress(false);
      return;
    }

    currentTaskId = data.task_id;
    apiFetch('/api/usage').then(r => r.json()).then(updateQuota).catch(() => {});
    startSSE(currentTaskId);

  } catch (err) {
    showError('Gagal menghubungi server: ' + err.message);
    setLoading(false);
    showTimelineProgress(false);
  }
});

// ── Preview Button Click ─────────────────────────────────────────────────
const btnPreview = $('btn-preview');
if (btnPreview) {
  btnPreview.addEventListener('click', () => {
    const start = startInput.value.trim();
    const end = endInput.value.trim();
    if (!start || !end) {
      alert('Isi waktu mulai dan selesai terlebih dahulu.');
      return;
    }
    const startSec = parseTimeToSeconds(start);
    const endSec = parseTimeToSeconds(end);

    // If a video is already loaded in the workspace player, seek & play segment
    if (workspaceVideoPlayer && workspaceVideoPlayer.src) {
      workspaceVideoPlayer.currentTime = startSec;
      workspaceVideoPlayer.play();
      showWorkspacePlayer(workspaceVideoPlayer.src);
      const stopAt = () => {
        if (workspaceVideoPlayer.currentTime >= endSec) {
          workspaceVideoPlayer.pause();
          workspaceVideoPlayer.removeEventListener('timeupdate', stopAt);
        }
      };
      workspaceVideoPlayer.addEventListener('timeupdate', stopAt);
      return;
    }

    // No video loaded yet: inform user
    alert('Preview langsung tersedia setelah video di-cache atau setelah klip pertama selesai. Anda tetap bisa mengatur start/end di timeline.');
  });
}

// ── Server-Sent Events ───────────────────────────────────────────────────
function startSSE(taskId) {
  if (evtSource) evtSource.close();

  evtSource = new EventSource(`/progress/${taskId}`);

  evtSource.onmessage = e => {
    const data = JSON.parse(e.data);
    handleUpdate(data);
  };

  evtSource.onerror = () => {
    evtSource.close();
    if (progressFill.style.width !== '100%') {
      showError('Koneksi ke server terputus.');
      setLoading(false);
      showTimelineProgress(false);
    }
  };
}

function handleUpdate(data) {
  // Progress bar
  const pct = data.progress || 0;
  progressFill.style.width = pct + '%';
  progressPct.textContent  = pct + '%';

  // Also update panel duplicates
  if (progressFillPanel) progressFillPanel.style.width = pct + '%';
  if (progressPctPanel)  progressPctPanel.textContent  = pct + '%';

  // Status label
  const statusMap = {
    pending:    '⏳ Menunggu...',
    queued:     '⏳ Antrian...',
    downloading:'⬇️  Mengunduh video...',
    subtitles:  '💬 Memproses subtitle...',
    tracking:   '🎯 Melacak wajah pembicara...',
    cutting:    '✂️  Memotong video...',
    embedding:  '🔡 Menyisipkan subtitle...',
    processing: '⚙️  Memproses video...',
    uploading:  '☁️  Mengunggah hasil...',
    done:       '✅ Selesai!',
    error:      '❌ Error',
    cancelling: '⏹️ Membatalkan...',
    cancelled:  '⏹️ Dibatalkan',
  };
  statusLabel.textContent = statusMap[data.status] || data.status;
  if (statusLabelPanel) statusLabelPanel.textContent = statusMap[data.status] || data.status;

  // Pulse color
  statusPulse.className = 'pulse';
  if (data.status === 'done')  statusPulse.classList.add('done');
  if (data.status === 'error') statusPulse.classList.add('error');
  if (statusPulsePanel) {
    statusPulsePanel.className = 'pulse';
    if (data.status === 'done')  statusPulsePanel.classList.add('done');
    if (data.status === 'error') statusPulsePanel.classList.add('error');
  }

  // Update workspace processing state
  updateWorkspaceProgress(pct, statusMap[data.status] || data.status, data.status);

  // Append new log lines
  if (data.logs && data.logs.length > 0) {
    data.logs.forEach(line => appendLog(line));
  }

  // Done
  if (data.status === 'done' && data.file) {
    evtSource.close();
    setLoading(false);
    showDownload(data.file, data.download_url || data.clip_url);
    showTimelineProgress(false);

    // Fetch extended metadata (virality score + thumbnail) for single clip
    if (currentTaskId) {
      fetch(`/task-meta/${currentTaskId}`)
        .then(r => r.ok ? r.json() : null)
        .then(meta => {
          if (!meta) return;
          const metaWrap = $('single-clip-meta');
          const scoreEl = $('single-clip-score');
          const reasonEl = $('single-clip-reason');
          const thumbEl = $('single-clip-thumb');
          if (meta.virality_score != null && metaWrap && scoreEl) {
            metaWrap.style.display = 'block';
            const scoreClass = meta.virality_score >= 75 ? 'high' : meta.virality_score >= 50 ? 'medium' : 'low';
            scoreEl.className = `modal-meta-score ${scoreClass}`;
            scoreEl.textContent = meta.virality_score;
          }
          if (meta.virality_reason && reasonEl) reasonEl.textContent = meta.virality_reason;
          if ((meta.thumbnail_url || meta.thumbnail_file) && thumbEl) {
            thumbEl.src = meta.thumbnail_url || `/download-thumb/${meta.thumbnail_file}`;
            thumbEl.style.display = 'block';
          }
        })
        .catch(() => {});
    }

    // Otomatis generate copy jika API key sudah ada
    if (geminiKeyInput && geminiKeyInput.value.trim() !== '') {
      btnGenerateAi.click();
    }
  }

  // Error or cancelled
  if (data.status === 'error' || data.status === 'cancelled') {
    evtSource.close();
    setLoading(false);
    showError(data.status === 'cancelled' ? 'Proses dibatalkan.' : (data.error || 'Terjadi kesalahan.'));
    showTimelineProgress(false);
    // Go back to info or empty
    if (cachedVideoInfo) {
      switchWorkspaceState('info');
    } else {
      switchWorkspaceState('empty');
    }
  }
}

// ── Log Terminal ─────────────────────────────────────────────────────────
function appendLog(line) {
  const el = document.createElement('div');
  el.classList.add('log-line');

  if (line.includes('❌') || line.toLowerCase().includes('error')) {
    el.classList.add('error-line');
  } else if (line.includes('✅') || line.includes('🎉')) {
    el.classList.add('success-line');
  } else if (line.includes('⬇️') || line.includes('✂️') || line.includes('ℹ️')) {
    el.classList.add('info-line');
  }

  el.textContent = line;
  terminalBody.appendChild(el);
  terminalBody.scrollTop = terminalBody.scrollHeight;
}

// ── UI Helpers ───────────────────────────────────────────────────────────
function setLoading(loading) {
  btnClip.disabled = loading;
  btnClip.classList.toggle('loading', loading);
}

function showDownload(filename, signedUrl = '') {
  const fileUrl = signedUrl || `/download/${filename}`;
  if (downloadLink) downloadLink.href = fileUrl;
  if (downloadLinkPanel) downloadLinkPanel.href = fileUrl;
  if (downloadName) downloadName.textContent = filename;
  
  // Set preview video in panel
  if (previewVideo) previewVideo.src = fileUrl;
  
  // Show download card in panel
  downloadCard.classList.add('visible');

  // Show video in main workspace player state
  showWorkspacePlayer(fileUrl, filename);
}

function showError(msg) {
  errorMsg.textContent = msg;
  errorCard.classList.add('visible');
}

function resetUI() {
  progressFill.style.width = '0%';
  progressPct.textContent  = '0%';
  statusLabel.textContent  = '⏳ Menunggu...';
  statusPulse.className    = 'pulse';
  downloadCard.classList.remove('visible');
  errorCard.classList.remove('visible');
  terminalBody.innerHTML   = '';
  
  if (progressFillPanel) progressFillPanel.style.width = '0%';
  if (progressPctPanel)  progressPctPanel.textContent  = '0%';
  if (statusLabelPanel)  statusLabelPanel.textContent  = '⏳ Menunggu...';
  if (statusPulsePanel)  statusPulsePanel.className    = 'pulse';
  
  // Reset preview video
  if (previewVideo) {
    previewVideo.pause();
    previewVideo.removeAttribute('src');
    previewVideo.load();
  }
}

// ═══════════════════════════════════════════════════════════════════
// WORKSPACE STATE MANAGEMENT
// ═══════════════════════════════════════════════════════════════════

function switchWorkspaceState(state) {
  // state: 'empty', 'info', 'processing', 'player', 'gallery'
  const states = [wsEmpty, wsInfo, wsProcessing, wsPlayer, wsGallery];
  const map = { empty: wsEmpty, info: wsInfo, processing: wsProcessing, player: wsPlayer, gallery: wsGallery };
  
  // Pause any playing video when leaving player state
  if (workspaceVideoPlayer && !map[state]?.classList.contains('active')) {
    workspaceVideoPlayer.pause();
  }
  
  states.forEach(el => { if (el) el.classList.remove('active'); });
  const target = map[state];
  if (target) target.classList.add('active');
}

// ── Show video info in workspace ─────────────────────────────────────────
function showWorkspaceInfo(info) {
  cachedVideoInfo = info;
  const thumb = $('ws-info-thumb');
  const title = $('ws-info-title');
  const channel = $('ws-info-channel');
  const duration = $('ws-info-duration');
  const views = $('ws-info-views');
  
  if (thumb) thumb.src = info.thumbnail || '';
  if (title) title.textContent = info.title || 'Video';
  if (channel) channel.textContent = info.channel || '';
  if (duration) duration.textContent = `⏱ ${info.duration_str || '00:00:00'}`;
  if (views) {
    const vc = info.view_count || 0;
    const formatted = vc >= 1000000 ? (vc / 1000000).toFixed(1) + 'M'
                    : vc >= 1000 ? (vc / 1000).toFixed(1) + 'K'
                    : String(vc);
    views.textContent = `👁 ${formatted}`;
  }
  
  switchWorkspaceState('info');
}

// ── Show processing state ────────────────────────────────────────────────
function showWorkspaceProcessing() {
  // Transfer thumbnail from info state
  const processingThumb = $('ws-processing-thumb');
  if (processingThumb && cachedVideoInfo?.thumbnail) {
    processingThumb.src = cachedVideoInfo.thumbnail;
  } else if (processingThumb) {
    processingThumb.src = '';
  }
  
  // Reset progress ring
  updateWorkspaceProgress(0, '⏳ Memproses video...', 'pending');
  switchWorkspaceState('processing');
}

// ── Update processing progress ring ──────────────────────────────────────
function updateWorkspaceProgress(pct, statusText, status) {
  const ring = $('ws-ring-progress');
  const ringPct = $('ws-ring-pct');
  const statusEl = $('ws-processing-status');
  const substatusEl = $('ws-processing-substatus');
  
  if (ring) {
    const circumference = 2 * Math.PI * 42; // r=42
    const offset = circumference - (pct / 100) * circumference;
    ring.style.strokeDashoffset = offset;
  }
  if (ringPct) ringPct.textContent = pct + '%';
  if (statusEl) statusEl.textContent = statusText;
  
  // Substatus hints
  if (substatusEl) {
    const hints = {
      pending: 'Menyiapkan...',
      queued: 'Menunggu worker tersedia...',
      downloading: 'Mengunduh dari server...',
      subtitles: 'Mengekstrak dan memproses subtitle...',
      tracking: 'Mendeteksi wajah dan melacak posisi...',
      cutting: 'Memotong segmen video...',
      embedding: 'Menyisipkan subtitle ke video...',
      processing: 'Rendering video output...',
      done: 'Proses selesai!',
      error: 'Terjadi kesalahan.',
      cancelling: 'Membatalkan proses...',
      cancelled: 'Proses dibatalkan.',
    };
    substatusEl.textContent = hints[status] || 'Mohon tunggu...';
  }
}

// ── Show video player state ──────────────────────────────────────────────
function showWorkspacePlayer(fileUrl, filename) {
  if (workspaceVideoPlayer) {
    workspaceVideoPlayer.src = fileUrl;
  }
  const filenameEl = $('ws-player-filename');
  if (filenameEl) filenameEl.textContent = filename || '';
  
  if (downloadLink) downloadLink.href = fileUrl;
  
  switchWorkspaceState('player');
}

// ── Show gallery state ───────────────────────────────────────────────────
function showWorkspaceGallery(tasks) {
  const grid = $('ws-gallery-grid');
  const titleEl = $('ws-gallery-title');
  if (!grid) return;
  
  grid.innerHTML = '';
  const successTasks = tasks.filter(t => t.output_file);
  
  if (titleEl) {
    titleEl.textContent = `${successTasks.length} Klip Berhasil!`;
  }
  
  successTasks.forEach(t => {
    const fileUrl = t.download_url || t.clip_url || `/download/${t.output_file}`;
    const thumbUrl = t.thumbnail_url || (t.thumbnail_file ? `/download-thumb/${t.thumbnail_file}` : '');
    const score = t.virality_score != null ? t.virality_score : null;
    const scoreClass = score >= 75 ? 'high' : score >= 50 ? 'medium' : 'low';
    const card = document.createElement('div');
    card.className = 'ws-gallery-card';
    card.innerHTML = `
      <div class="ws-gallery-thumb-wrap">
        ${thumbUrl ? `<img class="ws-gallery-thumb" src="${thumbUrl}" alt="thumbnail" />` : `<video src="${fileUrl}" preload="metadata" muted></video>`}
        ${score != null ? `<span class="ws-gallery-score ${scoreClass}">${score}</span>` : ''}
      </div>
      <div class="ws-gallery-card-info">
        <p class="ws-gallery-card-title">${escHtml(t.title || `Momen ${t.moment_index}`)}</p>
        <p class="ws-gallery-card-time">⏱ ${t.start} → ${t.end}</p>
        ${score != null ? `<p class="ws-gallery-score-reason" title="${escHtml(t.virality_reason || '')}">${escHtml(t.virality_reason || '')}</p>` : ''}
        <div class="ws-gallery-card-actions">
          <a href="${fileUrl}" download>⬇️ Download</a>
          ${thumbUrl ? `<a href="${thumbUrl}" download class="thumb-download">🖼️ Thumbnail</a>` : ''}
          <button onclick="window.openClipDetailsModal('${fileUrl}', '${t.start}', '${t.end}', ${t.moment_index}, '${t.task_id}')">✨ Detail</button>
        </div>
      </div>
    `;
    
    // Click video thumbnail to play in workspace player
    const mediaEl = card.querySelector('video, img');
    if (mediaEl) {
      mediaEl.addEventListener('click', () => {
        showWorkspacePlayer(fileUrl, t.output_file);
        highlightTimeline(t.start, t.end);
      });
    }
    
    grid.appendChild(card);
  });
  
  switchWorkspaceState('gallery');
}

function showTimelineProgress(show) {
  if (timelineProgressSection) {
    timelineProgressSection.classList.toggle('visible', show);
  }
}

// ── Auto-fetch video info on URL input ────────────────────────────────────
let urlFetchTimeout = null;

function setupUrlAutoFetch(inputEl) {
  if (!inputEl) return;
  
  inputEl.addEventListener('blur', () => {
    const url = inputEl.value.trim();
    if (url && url.startsWith('http')) {
      triggerVideoInfoFetch(url);
    }
  });
  
  inputEl.addEventListener('input', () => {
    if (urlFetchTimeout) clearTimeout(urlFetchTimeout);
    const url = inputEl.value.trim();
    if (url && url.startsWith('http') && url.length > 15) {
      urlFetchTimeout = setTimeout(() => triggerVideoInfoFetch(url), 1200);
    }
  });
  
  // Handle paste event for instant feedback
  inputEl.addEventListener('paste', () => {
    setTimeout(() => {
      const url = inputEl.value.trim();
      if (url && url.startsWith('http')) {
        triggerVideoInfoFetch(url);
      }
    }, 100);
  });
}

async function triggerVideoInfoFetch(url) {
  // Don't re-fetch if we already have info for this URL
  if (cachedVideoInfo && cachedVideoInfo._url === url) {
    switchWorkspaceState('info');
    return;
  }
  
  // Abort previous fetch
  if (videoInfoFetchController) videoInfoFetchController.abort();
  videoInfoFetchController = new AbortController();
  
  // Show loading in workspace
  switchWorkspaceState('empty');
  const emptyTitle = wsEmpty?.querySelector('.ws-empty-title');
  const emptyHint = wsEmpty?.querySelector('.ws-empty-hint');
  const emptyIcon = wsEmpty?.querySelector('.ws-empty-icon');
  if (emptyTitle) emptyTitle.textContent = 'Mengambil info video...';
  if (emptyHint) emptyHint.textContent = 'Memuat thumbnail dan metadata...';
  if (emptyIcon) emptyIcon.style.animationDuration = '1s';
  
  try {
    const res = await apiFetch('/video-info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
      signal: videoInfoFetchController.signal,
    });
    const data = await res.json();
    
    if (data.error) {
      resetEmptyState();
      return;
    }
    
    data._url = url; // Cache the URL
    showWorkspaceInfo(data);
    
    // Set timeline duration from video metadata
    if (data.duration) {
      setTimelineDuration(data.duration);
    }
    
    // Also update timeline with duration info
    if (timelineInfo) {
      timelineInfo.textContent = `00:00:00 — ${data.duration_str || '00:00:00'}`;
    }
    
  } catch (err) {
    if (err.name !== 'AbortError') {
      resetEmptyState();
    }
  }
}

function resetEmptyState() {
  const emptyTitle = wsEmpty?.querySelector('.ws-empty-title');
  const emptyHint = wsEmpty?.querySelector('.ws-empty-hint');
  const emptyIcon = wsEmpty?.querySelector('.ws-empty-icon');
  if (emptyTitle) emptyTitle.textContent = 'Mulai dengan Memasukkan URL';
  if (emptyHint) emptyHint.textContent = 'Paste URL video dari YouTube, TikTok, Instagram, atau 1000+ platform lainnya';
  if (emptyIcon) emptyIcon.style.animationDuration = '3s';
  switchWorkspaceState('empty');
}

// Setup auto-fetch on both URL inputs
setupUrlAutoFetch(urlInput);
setupUrlAutoFetch($('ctrl-url'));

// ── "Clip Lagi" buttons ───────────────────────────────────────────────────
function handleNewClip() {
  progressSection.classList.remove('visible');
  downloadCard.classList.remove('visible');
  errorCard.classList.remove('visible');
  urlInput.value   = '';
  startInput.value = '';
  endInput.value   = '';
  cachedVideoInfo  = null;
  
  // Reset workspace video player
  if (workspaceVideoPlayer) {
    workspaceVideoPlayer.pause();
    workspaceVideoPlayer.removeAttribute('src');
  }
  
  // Reset single clip metadata
  const metaWrap = $('single-clip-meta');
  const scoreEl = $('single-clip-score');
  const reasonEl = $('single-clip-reason');
  const thumbEl = $('single-clip-thumb');
  if (metaWrap) metaWrap.style.display = 'none';
  if (scoreEl) scoreEl.textContent = '0';
  if (reasonEl) reasonEl.textContent = '';
  if (thumbEl) {
    thumbEl.src = '';
    thumbEl.style.display = 'none';
  }
  
  resetEmptyState();
  clearTimelineHighlight();
  urlInput.focus();
}

$('btn-new').addEventListener('click', handleNewClip);
const btnNewPanel = $('btn-new-panel');
if (btnNewPanel) btnNewPanel.addEventListener('click', handleNewClip);

// ── Time Input Helper: auto-format to HH:MM:SS ───────────────────────────
function formatTimeInput(input) {
  input.addEventListener('blur', () => {
    const v = input.value.trim();
    if (!v) return;

    // If pure number, treat as seconds and convert
    if (/^\d+(\.\d+)?$/.test(v)) {
      const secs  = parseFloat(v);
      const h     = Math.floor(secs / 3600);
      const m     = Math.floor((secs % 3600) / 60);
      const s     = (secs % 60).toFixed(0).padStart(2, '0');
      input.value = `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${s}`;
    }

    // Update timeline when time inputs change
    if (startInput.value && endInput.value) {
      highlightTimeline(startInput.value, endInput.value);
    }
  });
}

formatTimeInput(startInput);
formatTimeInput(endInput);


// ── Position & Style row: show only for burn-in ──────────────────────────
function syncPositionRow() {
  if (subtitleType.value === 'burn') {
    positionRow.style.display = 'block';
    styleRow.style.display = 'block';
  } else {
    positionRow.style.display = 'none';
    styleRow.style.display = 'none';
  }
}

subtitleType.addEventListener('change', syncPositionRow);

// ── Auto Generate Hook Title ───────────────────────────────────────────────
const btnGenerateHook = $('btn-generate-hook');
const hookTitleInput = $('hook-title');

if (btnGenerateHook) {
  btnGenerateHook.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    const start = startInput.value.trim();
    const end = endInput.value.trim();
    // Re-use geminiKeyInput for Groq
    const apiKey = geminiKeyInput ? geminiKeyInput.value.trim() : '';

    if (!url) return alert('Silakan masukkan URL video terlebih dahulu!');

    const oldContent = btnGenerateHook.innerHTML;
    btnGenerateHook.disabled = true;
    btnGenerateHook.innerHTML = '<span class="btn-icon">⏳</span>...';

    try {
      const cookies = $('manual-cookies-toggle') ? $('manual-cookies-toggle').checked : false;

      const res = await apiFetch('/generate-hook', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, api_key: apiKey, start, end, cookies, language }),
      });
      
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Terjadi kesalahan');

      hookTitleInput.value = data.hook_title;
    } catch (err) {
      alert('Error AI: ' + err.message);
    } finally {
      btnGenerateHook.disabled = false;
      btnGenerateHook.innerHTML = oldContent;
    }
  });
}

// ── AI Copywriter Logic ──────────────────────────────────────────────────
const geminiKeyInput = $('gemini-key');
const btnGenerateAi  = $('btn-generate-ai');
const aiSpinner      = $('ai-spinner');
const aiBtnLabel     = $('ai-btn-label');
const aiResultWrap   = $('ai-result-wrap');
const aiResult       = $('ai-result');
const btnCopyAi      = $('btn-copy-ai');
const aiLanguage     = $('ai-language');
const modalAiLanguage = $('modal-ai-language');

// API keys are persisted encrypted on the server, never in browser storage.
localStorage.removeItem('clipper_gemini_key');

// Load saved language preference
const savedAiLang = localStorage.getItem('clipper_ai_language') || 'id';
if (aiLanguage) aiLanguage.value = savedAiLang;
if (modalAiLanguage) modalAiLanguage.value = savedAiLang;

// Sync language selectors
function syncAiLanguage(lang) {
  if (aiLanguage) aiLanguage.value = lang;
  if (modalAiLanguage) modalAiLanguage.value = lang;
  localStorage.setItem('clipper_ai_language', lang);
}

if (aiLanguage) {
  aiLanguage.addEventListener('change', () => syncAiLanguage(aiLanguage.value));
}
if (modalAiLanguage) {
  modalAiLanguage.addEventListener('change', () => syncAiLanguage(modalAiLanguage.value));
}

// Labels for AI copy sections per language
const AI_LABELS = {
  id: {
    title: '🌟 Judul Video',
    caption: '📝 Caption',
    cta: '🔥 Call to Action (CTA)',
    hashtags: '🏷️ Hashtags',
    copyBtn: '📋 Copy Semua',
    copied: '✅ Berhasil Disalin!'
  },
  en: {
    title: '🌟 Video Title',
    caption: '📝 Caption',
    cta: '🔥 Call to Action (CTA)',
    hashtags: '🏷️ Hashtags',
    copyBtn: '📋 Copy All',
    copied: '✅ Copied!'
  }
};

function updateModalCopyLabels(language) {
  const labels = AI_LABELS[language] || AI_LABELS.id;
  const titleEl = clipModal.querySelector('.ai-section:nth-of-type(1) h4');
  const captionEl = clipModal.querySelector('.ai-section:nth-of-type(2) h4');
  const ctaEl = clipModal.querySelector('.ai-section:nth-of-type(3) h4');
  const tagsEl = clipModal.querySelector('.ai-section:nth-of-type(4) h4');
  if (titleEl) titleEl.textContent = labels.title;
  if (captionEl) captionEl.textContent = labels.caption;
  if (ctaEl) ctaEl.textContent = labels.cta;
  if (tagsEl) tagsEl.textContent = labels.hashtags;
  if (modalBtnCopyAll) modalBtnCopyAll.textContent = labels.copyBtn;
}

btnGenerateAi.addEventListener('click', async () => {
  const url = urlInput.value.trim();
  const start = startInput.value.trim();
  const end = endInput.value.trim();
  const apiKey = geminiKeyInput.value.trim();

  if (!url) return alert('Silakan masukkan URL video terlebih dahulu!');
  const language = aiLanguage ? aiLanguage.value : 'id';
  syncAiLanguage(language);

  // UI state
  btnGenerateAi.disabled = true;
  aiSpinner.style.display = 'inline-block';
  aiBtnLabel.textContent = 'Menganalisis Video...';
  aiResultWrap.style.display = 'none';
  btnCopyAi.style.display = 'none';

  // Switch to AI tab
  switchPanelTab('panel-ai');

  const cookies = $('manual-cookies-toggle') ? $('manual-cookies-toggle').checked : false;

  try {
    const res = await apiFetch('/generate-copy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, api_key: apiKey, start, end, cookies, language }),
    });
    
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Terjadi kesalahan');

    const formatted = formatCopyResponse(data, language);
    aiResult.value = formatted;
    aiResultWrap.style.display = 'block';
    btnCopyAi.style.display = 'inline-block';
  } catch (err) {
    alert('Error AI: ' + err.message);
  } finally {
    btnGenerateAi.disabled = false;
    aiSpinner.style.display = 'none';
    aiBtnLabel.textContent = '✨ Generate Copy';
  }
});

btnCopyAi.addEventListener('click', () => {
  aiResult.select();
  document.execCommand('copy');
  const oldText = btnCopyAi.textContent;
  btnCopyAi.textContent = '✅ Disalin!';
  setTimeout(() => btnCopyAi.textContent = oldText, 2000);
});

// ── Init ─────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  checkDeps();
  syncPositionRow();   // set initial state
  renderPresetCards(); // build CapCut-style preset cards
  generateWaveform(); // generate fake timeline waveform
  // Don't auto-focus to prevent scroll issues in dashboard
});

// ── Subtitle Preset Cards ─────────────────────────────────────────────────
const SUBTITLE_PRESETS = [
  {
    id: 'none', name: 'Tanpa\\nPreset',
    preview: { text: 'Aa', color: '#888', shadow: 'none', bg: 'transparent', weight: 'normal', style: 'normal', deco: 'none' },
    params: null // don't override any params
  },
  {
    id: 'classic', name: 'Classic',
    preview: { text: 'Aa', color: '#fff', shadow: '-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000', bg: 'transparent', weight: 'normal', style: 'normal', deco: 'none' },
    params: { fontSize: '22', bold: false, italic: false, underline: false, case: 'normal', primaryColor: 'FFFFFF', outlineColor: '000000', backColor: '000000', backAlpha: '00', borderStyle: '1', outlineWidth: '2', shadow: '1' }
  },
  {
    id: 'bold-pop', name: 'Bold Pop',
    preview: { text: 'AA', color: '#fff', shadow: '-2px -2px 0 #000,2px -2px 0 #000,-2px 2px 0 #000,2px 2px 0 #000', bg: 'transparent', weight: '900', style: 'normal', deco: 'none' },
    params: { fontSize: '26', bold: true, italic: false, underline: false, case: 'upper', primaryColor: 'FFFFFF', outlineColor: '000000', backColor: '000000', backAlpha: '00', borderStyle: '1', outlineWidth: '3', shadow: '2' }
  },
  {
    id: 'yellow-pop', name: 'Yellow\\nPop',
    preview: { text: 'AA', color: '#FFFF00', shadow: '-2px -2px 0 #000,2px -2px 0 #000,-2px 2px 0 #000,2px 2px 0 #000', bg: 'transparent', weight: '900', style: 'normal', deco: 'none' },
    params: { fontSize: '26', bold: true, italic: false, underline: false, case: 'upper', primaryColor: 'FFFF00', outlineColor: '000000', backColor: '000000', backAlpha: '00', borderStyle: '1', outlineWidth: '3', shadow: '2' }
  },
  {
    id: 'white-box', name: 'White\\nBox',
    preview: { text: 'Aa', color: '#fff', shadow: 'none', bg: 'rgba(0,0,0,0.75)', weight: 'normal', style: 'normal', deco: 'none' },
    params: { fontSize: '22', bold: false, italic: false, underline: false, case: 'normal', primaryColor: 'FFFFFF', outlineColor: '000000', backColor: '000000', backAlpha: '80', borderStyle: '3', outlineWidth: '3', shadow: '0' }
  },
  {
    id: 'yellow-box', name: 'Yellow\\nBox',
    preview: { text: 'Aa', color: '#FFFF00', shadow: 'none', bg: 'rgba(0,0,0,0.85)', weight: 'bold', style: 'normal', deco: 'none' },
    params: { fontSize: '22', bold: true, italic: false, underline: false, case: 'normal', primaryColor: 'FFFF00', outlineColor: '000000', backColor: '000000', backAlpha: 'CC', borderStyle: '3', outlineWidth: '3', shadow: '0' }
  },
  {
    id: 'black-white-box', name: 'White Box\\nBlack Text',
    preview: { text: 'Aa', color: '#000', shadow: 'none', bg: '#fff', weight: 'bold', style: 'normal', deco: 'none' },
    params: { fontSize: '22', bold: true, italic: false, underline: false, case: 'upper', primaryColor: '000000', outlineColor: 'FFFFFF', backColor: 'FFFFFF', backAlpha: '00', borderStyle: '3', outlineWidth: '3', shadow: '0' }
  },
  {
    id: 'neon', name: 'Neon\\nGlow',
    preview: { text: 'AA', color: '#00FFFF', shadow: '0 0 6px #00FFFF,0 0 14px #00FFFF', bg: 'transparent', weight: 'bold', style: 'normal', deco: 'none' },
    params: { fontSize: '24', bold: true, italic: false, underline: false, case: 'upper', primaryColor: '00FFFF', outlineColor: '003333', backColor: '000000', backAlpha: '00', borderStyle: '1', outlineWidth: '2', shadow: '5' }
  },
  {
    id: 'cinema', name: 'Cinema',
    preview: { text: 'Aa', color: '#fff', shadow: '2px 2px 10px rgba(0,0,0,0.9)', bg: 'transparent', weight: 'normal', style: 'italic', deco: 'none' },
    params: { fontSize: '20', bold: false, italic: true, underline: false, case: 'normal', primaryColor: 'FFFFFF', outlineColor: '000000', backColor: '000000', backAlpha: '00', borderStyle: '1', outlineWidth: '1', shadow: '5' }
  },
  {
    id: 'tiktok', name: 'TikTok\\nRed',
    preview: { text: 'AA', color: '#FF3B5C', shadow: '-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff', bg: 'transparent', weight: '900', style: 'normal', deco: 'none' },
    params: { fontSize: '26', bold: true, italic: false, underline: false, case: 'upper', primaryColor: 'FF3B5C', outlineColor: 'FFFFFF', backColor: '000000', backAlpha: '00', borderStyle: '1', outlineWidth: '2', shadow: '0' }
  },
];

// ── Shared helper: render preset cards into any container ────────
function renderPresetsInto(container, applyFn) {
  container.innerHTML = '';
  SUBTITLE_PRESETS.forEach((preset, idx) => {
    const card = document.createElement('div');
    card.className = 'preset-card' + (idx === 0 ? ' active' : '');
    card.dataset.presetId = preset.id;

    const p = preset.preview;
    const boxBg = p.bg !== 'transparent'
      ? `background:${p.bg}; padding:2px 5px; border-radius:3px;`
      : '';
    const nameLines = preset.name.split('\\n').join('<br>');

    card.innerHTML = `
      <div class="preset-preview-box">
        <span class="preset-preview-text" style="color:${p.color};text-shadow:${p.shadow};font-weight:${p.weight};font-style:${p.style};text-decoration:${p.deco};${boxBg}">${p.text}</span>
      </div>
      <div class="preset-card-name">${nameLines}</div>
    `;

    card.addEventListener('click', () => {
      // Scope active-state removal to THIS container only
      container.querySelectorAll('.preset-card').forEach(c => c.classList.remove('active'));
      card.classList.add('active');
      applyFn(preset);
    });

    container.appendChild(card);
  });
}

function renderPresetCards() {
  const manual = $('preset-cards');
  if (manual) renderPresetsInto(manual, applyPreset);

  const auto = $('ctrl-preset-cards');
  if (auto) renderPresetsInto(auto, applyCtrlPreset);
}

// Apply preset to manual-clip controls
function applyPreset(preset) {
  if (!preset.params) return;
  const p = preset.params;
  if (subFontsize)   subFontsize.value    = p.fontSize;
  if (subCase)       subCase.value        = p.case;
  if (subBold)       subBold.checked      = p.bold;
  if (subItalic)     subItalic.checked    = p.italic;
  if (subUnderline)  subUnderline.checked = p.underline;
  if (subPrimaryColor) subPrimaryColor.value = p.primaryColor;
  if (subOutlineColor) subOutlineColor.value = p.outlineColor;
  if (subBackColor)    subBackColor.value    = p.backColor;
  if (subBackAlpha)    subBackAlpha.value    = p.backAlpha;
  if (subBorderStyle)  subBorderStyle.value  = p.borderStyle;
  if (subOutlineWidth) subOutlineWidth.value = p.outlineWidth;
  if (subShadowVal)    subShadowVal.value    = p.shadow;

  // Auto switch to burn mode for styled presets (anything except 'none')
  if (preset.id !== 'none' && subtitleType) {
    subtitleType.value = 'burn';
    syncPositionRow();
  }
}

// Apply preset to auto-clip controls
function applyCtrlPreset(preset) {
  if (!preset.params) return;
  const p = preset.params;
  const ctrlFontsize     = $('ctrl-sub-fontsize');
  const ctrlCase         = $('ctrl-sub-case');
  const ctrlPrimary      = $('ctrl-sub-primary-color');
  const ctrlOutline      = $('ctrl-sub-outline-color');
  const ctrlBack         = $('ctrl-sub-back-color');
  const ctrlBackAlpha    = $('ctrl-sub-back-alpha');
  const ctrlBorderStyle  = $('ctrl-sub-border-style');
  const ctrlOutlineWidth = $('ctrl-sub-outline-width');
  const ctrlShadow       = $('ctrl-sub-shadow-val');
  if (ctrlFontsize)     ctrlFontsize.value     = p.fontSize;
  if (ctrlCase)         ctrlCase.value         = p.case;
  if (ctrlPrimary)      ctrlPrimary.value      = p.primaryColor;
  if (ctrlOutline)      ctrlOutline.value      = p.outlineColor;
  if (ctrlBack)         ctrlBack.value         = p.backColor;
  if (ctrlBackAlpha)    ctrlBackAlpha.value    = p.backAlpha;
  if (ctrlBorderStyle)  ctrlBorderStyle.value  = p.borderStyle;
  if (ctrlOutlineWidth) ctrlOutlineWidth.value = p.outlineWidth;
  if (ctrlShadow)       ctrlShadow.value       = p.shadow;
}


// ════════════════════════════════════════════════════════════
// AUTO-CLIP MOMEN KONTROVERSIAL — Feature Logic
// ════════════════════════════════════════════════════════════

// ── State ────────────────────────────────────────────────────
let detectedMoments   = [];   // [{index, start, end, title, reason}]
let selectedMoments   = new Set(); // Set of moment indices that are checked
let batchTaskList     = [];   // [{task_id, moment_index, title, start, end}]
let batchPollInterval = null;
let ctrlVideoUrl      = '';   // URL yang digunakan saat scan terakhir

// ── DOM refs ─────────────────────────────────────────────────
const controversialToggle  = $('controversial-toggle');
const controversialBody    = $('controversial-body');
const controversialChevron = $('controversial-chevron');
const ctrlUrlInput         = $('ctrl-url');
const ctrlNumMoments       = $('ctrl-num-moments');
const ctrlApiKey           = $('ctrl-api-key');
const ctrlVideoFormat      = $('ctrl-video-format');
const ctrlDownloadResolution = $('ctrl-download-resolution');
const ctrlOutputResolution   = $('ctrl-output-resolution');
const ctrlOutputQuality      = $('ctrl-output-quality');
const ctrlCookiesToggle    = $('ctrl-cookies-toggle');
const ctrlSubtitleToggle   = $('ctrl-subtitle-toggle');
const btnScan              = $('btn-scan');
const scanSpinner          = $('scan-spinner');
const scanBtnLabel         = $('scan-btn-label');
const scanStatus           = $('scan-status');
const momentsResult        = $('moments-result');
const momentsResultTitle   = $('moments-result-title');
const transcriptBadge      = $('transcript-badge');
const btnSelectAll         = $('btn-select-all');
const momentCardsList      = $('moment-cards-list');
const btnClipMoments       = $('btn-clip-moments');
const clipMomentsSpinner   = $('clip-moments-spinner');
const clipMomentsLabel     = $('clip-moments-label');
const batchProgressArea    = $('batch-progress-area');
const batchTasksList       = $('batch-tasks-list');
const btnBatchNew          = $('btn-batch-new');
const batchDownloadGallery = $('batch-download-gallery');
const batchClipsGrid       = $('batch-clips-grid');
const btnBatchReset        = $('btn-batch-reset');

// ── Subtitle options panel show/hide ──────────────────────────
if (ctrlSubtitleToggle) {
  ctrlSubtitleToggle.addEventListener('change', () => {
    const panel = $('ctrl-subtitle-options');
    if (panel) panel.style.display = ctrlSubtitleToggle.checked ? 'block' : 'none';
  });
}

// ── Collapsible Toggle ────────────────────────────────────────
if (controversialToggle) {
  controversialToggle.addEventListener('click', () => {
    const isOpen = controversialBody.classList.toggle('open');
    controversialChevron.classList.toggle('open', isOpen);
  });
}

// ── Scan Button ───────────────────────────────────────────────
if (btnScan) {
  btnScan.addEventListener('click', async () => {
    const url       = ctrlUrlInput ? ctrlUrlInput.value.trim() : '';
    const apiKey    = ctrlApiKey  ? ctrlApiKey.value.trim()   : '';
    const numMoments = ctrlNumMoments ? parseInt(ctrlNumMoments.value) : 4;
    const cookies    = ctrlCookiesToggle ? ctrlCookiesToggle.checked : false;

    if (!url)    { alert('URL video wajib diisi!'); ctrlUrlInput.focus(); return; }
    // Sync to main AI copywriter input too
    if (geminiKeyInput) geminiKeyInput.value = apiKey;

    ctrlVideoUrl = url;

    // Reset previous results
    detectedMoments = [];
    selectedMoments.clear();
    momentsResult.style.display        = 'none';
    batchProgressArea.style.display    = 'none';
    batchDownloadGallery.style.display = 'none';
    momentCardsList.innerHTML          = '';
    batchTasksList.innerHTML           = '';
    batchClipsGrid.innerHTML           = '';

    // Loading state
    setScanLoading(true);
    showScanStatus('loading', '⏳ Menganalisis video dan mendeteksi momen kontroversial...');

    // Show processing in workspace
    showWorkspaceProcessing();

    try {
      const subtitleLangVal = $('ctrl-subtitle-lang') ? $('ctrl-subtitle-lang').value : 'id,en';
      const res  = await apiFetch('/detect-moments', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ url, api_key: apiKey, num_moments: numMoments, cookies, subtitle_lang: subtitleLangVal }),
      });
      const data = await res.json();

      if (!res.ok || data.error) {
        showScanStatus('error', '❌ ' + (data.error || 'Terjadi kesalahan.'));
        if (cachedVideoInfo) switchWorkspaceState('info'); else resetEmptyState();
        return;
      }

      detectedMoments = data.moments || [];
      if (detectedMoments.length === 0) {
        showScanStatus('error', '❌ AI tidak dapat menemukan momen. Coba lagi.');
        showWorkspacePlaceholder('default');
        return;
      }

      // Show transcript badge
      if (data.has_transcript && transcriptBadge) {
        transcriptBadge.style.display = 'inline-flex';
      } else if (transcriptBadge) {
        transcriptBadge.style.display = 'none';
      }

      // Update title
      const vTitle = data.video_title ? `"${data.video_title.slice(0, 50)}…"` : '';
      if (momentsResultTitle) {
        momentsResultTitle.textContent = `${detectedMoments.length} Momen Ditemukan ${vTitle}`;
      }

      showScanStatus('success', `✅ ${detectedMoments.length} momen berhasil dideteksi! ${data.has_transcript ? '📝 Berdasarkan transcript nyata' : '⚠️ Tanpa transcript — estimasi AI'} · Model: ${data.model_used || 'AI'}`);


      // Render moment cards
      renderMomentCards(detectedMoments);
      momentsResult.style.display = 'block';

      // Auto-select all
      detectedMoments.forEach(m => selectedMoments.add(m.index));
      syncMomentSelection();

      // Switch to Results tab to show moments
      switchPanelTab('panel-results');

      // Highlight first moment on timeline
      if (detectedMoments.length > 0) {
        const m = detectedMoments[0];
        highlightTimeline(m.start, m.end);
      }

      // Restore workspace to info if available
      if (cachedVideoInfo) switchWorkspaceState('info'); else resetEmptyState();

    } catch (err) {
      showScanStatus('error', '❌ Gagal menghubungi server: ' + err.message);
      showWorkspacePlaceholder('default');
    } finally {
      setScanLoading(false);
    }
  });
}

// ── Render Moment Cards ───────────────────────────────────────
function renderMomentCards(moments) {
  momentCardsList.innerHTML = '';
  moments.forEach(m => {
    const card = document.createElement('div');
    card.className = 'moment-card selected'; // all selected by default
    card.dataset.index = m.index;

    card.innerHTML = `
      <div class="moment-card-check">✓</div>
      <div class="moment-card-index">${m.index}</div>
      <div class="moment-card-info">
        <p class="moment-card-title">${escHtml(m.title)} <span class="viral-badge">🔥 VIRAL</span></p>
        <p class="moment-card-time">⏱ ${m.start} → ${m.end}</p>
        <p class="moment-card-reason">${escHtml(m.reason)}</p>
      </div>
    `;

    card.addEventListener('click', () => {
      const idx = m.index;
      if (selectedMoments.has(idx)) {
        selectedMoments.delete(idx);
        card.classList.remove('selected');
      } else {
        selectedMoments.add(idx);
        card.classList.add('selected');
      }
      updateClipMomentsBtn();

      // Highlight on timeline when clicking a moment
      highlightTimeline(m.start, m.end);
    });

    momentCardsList.appendChild(card);
  });
  updateClipMomentsBtn();
}

function syncMomentSelection() {
  document.querySelectorAll('.moment-card').forEach(card => {
    const idx = parseInt(card.dataset.index);
    card.classList.toggle('selected', selectedMoments.has(idx));
  });
  updateClipMomentsBtn();
}

function updateClipMomentsBtn() {
  const count = selectedMoments.size;
  if (clipMomentsLabel) {
    clipMomentsLabel.textContent = count > 0
      ? `✂️  Potong ${count} Momen Terpilih`
      : '✂️  Pilih minimal 1 momen';
  }
  if (btnClipMoments) btnClipMoments.disabled = count === 0;
}

// ── Select All Button ─────────────────────────────────────────
if (btnSelectAll) {
  btnSelectAll.addEventListener('click', () => {
    const allSelected = selectedMoments.size === detectedMoments.length;
    if (allSelected) {
      selectedMoments.clear();
      btnSelectAll.textContent = '☑ Pilih Semua';
    } else {
      detectedMoments.forEach(m => selectedMoments.add(m.index));
      btnSelectAll.textContent = '☐ Batal Pilih';
    }
    syncMomentSelection();
  });
}

// ── Clip Moments Button ───────────────────────────────────────
if (btnClipMoments) {
  btnClipMoments.addEventListener('click', async () => {
    if (selectedMoments.size === 0) return;

    const chosenMoments = detectedMoments.filter(m => selectedMoments.has(m.index));
    const apiKey = ctrlApiKey ? ctrlApiKey.value.trim() : '';
    const videoFormat     = ctrlVideoFormat ? ctrlVideoFormat.value : 'original';
    const cookies         = ctrlCookiesToggle ? ctrlCookiesToggle.checked : false;
    const subtitleEnabled = ctrlSubtitleToggle ? ctrlSubtitleToggle.checked : false;
    const hookFontsize    = $('ctrl-hook-fontsize') ? $('ctrl-hook-fontsize').value : '34';
    const hookStyle       = $('ctrl-hook-style') ? $('ctrl-hook-style').value : 'yellow-pop';

    // Loading state
    btnClipMoments.disabled = true;
    btnClipMoments.classList.add('loading');

    // Show processing in workspace
    showWorkspaceProcessing();

    try {
      const res = await apiFetch('/clip-moments', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          url:              ctrlVideoUrl,
          moments:          chosenMoments,
          video_format:     videoFormat,
          download_resolution: ctrlDownloadResolution ? ctrlDownloadResolution.value : 'best',
          output_resolution:   ctrlOutputResolution   ? ctrlOutputResolution.value   : '1080',
          output_quality:      ctrlOutputQuality      ? ctrlOutputQuality.value      : 'standard',
          cookies:          cookies,
          subtitle_enabled:  subtitleEnabled,
          subtitle_lang:     $('ctrl-subtitle-lang') ? $('ctrl-subtitle-lang').value : 'id,en',
          subtitle_type:     'burn',
          subtitle_auto:     true,
          subtitle_position: $('ctrl-sub-position')    ? $('ctrl-sub-position').value    : 'bottom',
          sub_fontsize:      $('ctrl-sub-fontsize')    ? $('ctrl-sub-fontsize').value    : '22',
          sub_case:          $('ctrl-sub-case')        ? $('ctrl-sub-case').value        : 'upper',
          subtitle_style:    $('subtitle-style')       ? $('subtitle-style').value       : 'hormozi',
          bgm_type:          $('bgm-type')             ? $('bgm-type').value             : 'none',
          auto_broll:        $('ctrl-broll-toggle')    ? $('ctrl-broll-toggle').checked  : false,
          sub_bold:          false,
          sub_italic:        false,
          sub_underline:     false,
          sub_primary_color: $('ctrl-sub-primary-color') ? $('ctrl-sub-primary-color').value : 'FFFFFF',
          sub_outline_color: $('ctrl-sub-outline-color') ? $('ctrl-sub-outline-color').value : '000000',
          sub_back_color:    $('ctrl-sub-back-color')    ? $('ctrl-sub-back-color').value    : '000000',
          sub_back_alpha:    $('ctrl-sub-back-alpha')    ? $('ctrl-sub-back-alpha').value    : '00',
          sub_border_style:  $('ctrl-sub-border-style')  ? $('ctrl-sub-border-style').value  : '1',
          sub_outline_width: $('ctrl-sub-outline-width') ? $('ctrl-sub-outline-width').value : '3',
          sub_shadow:        $('ctrl-sub-shadow-val')    ? $('ctrl-sub-shadow-val').value    : '2',
          hook_fontsize:     hookFontsize,
          hook_preset:       hookStyle,
          hook_position:     $('ctrl-hook-position')   ? $('ctrl-hook-position').value   : 'top',
          transcription_source: ctrlSubtitleSource ? ctrlSubtitleSource.value : 'auto',
          whisper_model:     ctrlWhisperModel ? ctrlWhisperModel.value : 'base',
        }),
      });
      const data = await res.json();

      if (!res.ok || data.error) {
        alert('Error: ' + (data.error || 'Terjadi kesalahan.'));
        btnClipMoments.disabled = false;
        btnClipMoments.classList.remove('loading');
        if (cachedVideoInfo) switchWorkspaceState('info'); else resetEmptyState();
        return;
      }

      batchTaskList = data.tasks || [];

      // Show batch progress UI
      momentsResult.style.display    = 'none';
      scanStatus.style.display       = 'none';
      batchProgressArea.style.display = 'block';
      batchDownloadGallery.style.display = 'none';

      // Render batch task items
      renderBatchTasks(batchTaskList);

      // Start polling
      startBatchPolling();

    } catch (err) {
      alert('Gagal menghubungi server: ' + err.message);
      btnClipMoments.disabled = false;
      btnClipMoments.classList.remove('loading');
      if (cachedVideoInfo) switchWorkspaceState('info'); else resetEmptyState();
    }
  });
}

// ── Render Batch Task UI ──────────────────────────────────────
function renderBatchTasks(tasks) {
  batchTasksList.innerHTML = '';
  tasks.forEach(t => {
    const item = document.createElement('div');
    item.className = 'batch-task-item';
    item.id = `batch-task-${t.task_id}`;
    item.innerHTML = `
      <div class="batch-task-header">
        <span class="batch-task-name">🎬 ${escHtml(t.title || `Momen ${t.moment_index}`)}</span>
        <span class="batch-task-time">${t.start} → ${t.end}</span>
        <span class="batch-task-pct" id="pct-${t.task_id}">0%</span>
      </div>
      <div class="batch-task-track">
        <div class="batch-task-fill" id="fill-${t.task_id}" style="width:0%"></div>
      </div>
    `;
    batchTasksList.appendChild(item);
  });
}

// ── Batch Progress Polling ────────────────────────────────────
function startBatchPolling() {
  if (batchPollInterval) clearInterval(batchPollInterval);

  batchPollInterval = setInterval(async () => {
    const taskIds = batchTaskList.map(t => t.task_id);
    try {
      const res  = await apiFetch('/batch-progress', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ task_ids: taskIds }),
      });
      const data = await res.json();
      const tasks = data.tasks || {};

      let allDone = true;
      let anyError = false;
      let totalPct = 0;

      batchTaskList.forEach(t => {
        const st = tasks[t.task_id];
        if (!st) return;

        const pct      = st.progress || 0;
        totalPct += pct;
        const status   = st.status;
        const itemEl   = $(`batch-task-${t.task_id}`);
        const fillEl   = $(`fill-${t.task_id}`);
        const pctEl    = $(`pct-${t.task_id}`);

        if (fillEl)  fillEl.style.width = pct + '%';
        if (pctEl)   pctEl.textContent  = pct + '%';
        if (itemEl) {
          itemEl.classList.toggle('done',  status === 'done');
          itemEl.classList.toggle('error', status === 'error');
        }

        if (status !== 'done' && status !== 'error') allDone = false;
        if (status === 'error') anyError = true;

        // Store file info back into task
        if (st.file) t.output_file = st.file;
        if (st.error) t.error_msg  = st.error;
        if (st.virality_score !== undefined) t.virality_score = st.virality_score;
        if (st.virality_reason !== undefined) t.virality_reason = st.virality_reason;
        if (st.thumbnail_file !== undefined) t.thumbnail_file = st.thumbnail_file;
        if (st.download_url) t.download_url = st.download_url;
        if (st.clip_url) t.clip_url = st.clip_url;
        if (st.thumbnail_url) t.thumbnail_url = st.thumbnail_url;
        if (st.moment_index !== undefined) t.moment_index = st.moment_index;
      });

      // Update central workspace progress ring
      const avgPct = batchTaskList.length > 0 ? Math.round(totalPct / batchTaskList.length) : 0;
      updateWorkspaceProgress(avgPct, '⚙️ Memproses Batch...', 'processing');

      // All finished
      if (allDone) {
        clearInterval(batchPollInterval);
        batchPollInterval = null;

        setTimeout(() => {
          batchProgressArea.style.display    = 'none';
          batchDownloadGallery.style.display = 'block';
          renderBatchGallery(batchTaskList);
          // Show gallery in workspace
          showWorkspaceGallery(batchTaskList);
        }, 800);
      }

    } catch (e) {
      // Silently ignore polling errors (server may be busy)
    }
  }, 800);
}

// ── Render Download Gallery ───────────────────────────────────
function renderBatchGallery(tasks) {
  batchClipsGrid.innerHTML = '';
  const successTasks = tasks.filter(t => t.output_file);

  if (successTasks.length === 0) {
    batchClipsGrid.innerHTML = '<p style="color:var(--accent-danger); font-size:.82rem;">Semua klip gagal diproses.</p>';
    return;
  }

  successTasks.forEach(t => {
    const fileUrl = t.download_url || t.clip_url || `/download/${t.output_file}`;
    const thumbUrl = t.thumbnail_url || (t.thumbnail_file ? `/download-thumb/${t.thumbnail_file}` : '');
    const score = t.virality_score != null ? t.virality_score : null;
    const scoreClass = score >= 75 ? 'high' : score >= 50 ? 'medium' : 'low';
    const card = document.createElement('div');
    card.className = 'batch-clip-card';
    card.innerHTML = `
      <div class="batch-clip-thumb-wrap">
        ${thumbUrl ? `<img class="batch-clip-thumb" src="${thumbUrl}" alt="thumbnail" />` : `<video class="batch-clip-video" src="${fileUrl}" preload="metadata" muted></video>`}
        ${score != null ? `<span class="batch-clip-score ${scoreClass}">${score}</span>` : ''}
      </div>
      <div class="batch-clip-info">
        <p class="batch-clip-title">${escHtml(t.title || `Momen ${t.moment_index}`)}</p>
        <p class="batch-clip-time">⏱ ${t.start} → ${t.end}</p>
        ${score != null ? `<p class="batch-clip-score-reason" title="${escHtml(t.virality_reason || '')}">${escHtml(t.virality_reason || '')}</p>` : ''}
        <div style="display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap;">
          <a href="${fileUrl}" download class="batch-clip-download" style="flex:1; text-align:center;">⬇️ Download</a>
          ${thumbUrl ? `<a href="${thumbUrl}" download class="batch-clip-download" style="flex:1; text-align:center;">🖼️ Thumbnail</a>` : ''}
          <button type="button" class="btn batch-clip-detail-btn" style="flex:1; padding: 6px;" onclick="window.openClipDetailsModal('${fileUrl}', '${t.start}', '${t.end}', ${t.moment_index}, '${t.task_id}')">✨ Detail</button>
        </div>
      </div>
    `;

    // Click on video in gallery -> play in workspace
    const mediaEl = card.querySelector('video, img');
    if (mediaEl) {
      mediaEl.addEventListener('click', () => {
        showWorkspacePlayer(fileUrl, t.output_file);
        highlightTimeline(t.start, t.end);
      });
    }

    batchClipsGrid.appendChild(card);
  });
}

// ── Reset Buttons ─────────────────────────────────────────────
function resetControversialUI() {
  if (batchPollInterval) { clearInterval(batchPollInterval); batchPollInterval = null; }
  batchTaskList   = [];
  detectedMoments = [];
  selectedMoments.clear();
  if (momentCardsList)       momentCardsList.innerHTML       = '';
  if (batchTasksList)        batchTasksList.innerHTML        = '';
  if (batchClipsGrid)        batchClipsGrid.innerHTML        = '';
  if (momentsResult)         momentsResult.style.display         = 'none';
  if (batchProgressArea)     batchProgressArea.style.display     = 'none';
  if (batchDownloadGallery)  batchDownloadGallery.style.display  = 'none';
  if (scanStatus)            scanStatus.style.display            = 'none';
  if (ctrlUrlInput)          ctrlUrlInput.value  = '';
  if (btnClipMoments)        btnClipMoments.classList.remove('loading');
  if (btnClipMoments)        btnClipMoments.disabled = false;

  cachedVideoInfo = null;
  if (workspaceVideoPlayer) {
    workspaceVideoPlayer.pause();
    workspaceVideoPlayer.removeAttribute('src');
  }
  resetEmptyState();
  clearTimelineHighlight();
}

if (btnBatchNew)   btnBatchNew.addEventListener('click',   resetControversialUI);
if (btnBatchReset) btnBatchReset.addEventListener('click', resetControversialUI);

// ── Helpers ───────────────────────────────────────────────────
function setScanLoading(loading) {
  if (!btnScan) return;
  btnScan.disabled = loading;
  btnScan.classList.toggle('loading', loading);
}

function showScanStatus(type, message) {
  if (!scanStatus) return;
  scanStatus.className = `scan-status ${type}`;
  scanStatus.textContent = message;
  scanStatus.style.display = 'block';
}

// ── Clip Details Modal Logic ──────────────────────────────────
const clipModal = $('clip-details-modal');
const modalCloseBtn = $('modal-close-btn');
const modalVideoPlayer = $('modal-video-player');
const modalAiSpinner = $('modal-ai-spinner');
const modalAiResult = $('modal-ai-result');
const modalTitleHooks = $('modal-title-hooks');
const modalCaption = $('modal-caption');
const modalCta = $('modal-cta');
const modalHashtags = $('modal-hashtags');
const modalBtnCopyAll = $('modal-btn-copy-all');

function closeClipDetailsModal() {
  clipModal.style.display = 'none';
  modalVideoPlayer.pause();
  modalVideoPlayer.src = '';
}

if (modalCloseBtn) {
  modalCloseBtn.addEventListener('click', closeClipDetailsModal);
}

// Close when clicking outside modal content
if (clipModal) {
  clipModal.addEventListener('click', (e) => {
    if (e.target === clipModal) closeClipDetailsModal();
  });
}

window.openClipDetailsModal = async function(fileUrl, start, end, momentIndex, taskId) {
  // Reset UI
  clipModal.style.display = 'flex';
  modalVideoPlayer.src = fileUrl;
  modalAiResult.style.display = 'none';
  modalAiSpinner.style.display = 'block';
  modalTitleHooks.innerHTML = '';
  modalCaption.innerHTML = '';
  modalCta.innerHTML = '';
  modalHashtags.innerHTML = '';

  // Reset virality metadata UI
  const modalMetaScore = $('modal-meta-score');
  const modalMetaReason = $('modal-meta-reason');
  const modalMetaThumb = $('modal-meta-thumb');
  if (modalMetaScore) modalMetaScore.style.display = 'none';
  if (modalMetaReason) modalMetaReason.textContent = '';
  if (modalMetaThumb) {
    modalMetaThumb.src = '';
    modalMetaThumb.style.display = 'none';
  }

  // Fetch task metadata if taskId available
  if (taskId) {
    try {
      const metaRes = await fetch(`/task-meta/${taskId}`);
      if (metaRes.ok) {
        const meta = await metaRes.json();
        if (meta.virality_score != null && modalMetaScore) {
          const scoreClass = meta.virality_score >= 75 ? 'high' : meta.virality_score >= 50 ? 'medium' : 'low';
          modalMetaScore.className = `modal-meta-score ${scoreClass}`;
          modalMetaScore.textContent = meta.virality_score;
          modalMetaScore.style.display = 'inline-flex';
        }
        if (meta.virality_reason && modalMetaReason) {
          modalMetaReason.textContent = meta.virality_reason;
        }
        if ((meta.thumbnail_url || meta.thumbnail_file) && modalMetaThumb) {
          modalMetaThumb.src = meta.thumbnail_url || `/download-thumb/${meta.thumbnail_file}`;
          modalMetaThumb.style.display = 'block';
        }
      }
    } catch (e) {
      console.warn('Failed to fetch task metadata:', e);
    }
  }

  const apiKey = ctrlApiKey ? ctrlApiKey.value : '';
  const url = ctrlVideoUrl || urlInput.value;
  const cookies = ctrlCookiesToggle ? ctrlCookiesToggle.checked : false;
  const language = modalAiLanguage ? modalAiLanguage.value : (aiLanguage ? aiLanguage.value : 'id');
  syncAiLanguage(language);

  const langLabels = AI_LABELS[language] || AI_LABELS.id;
  updateModalCopyLabels(language);

  if (!apiKey) {
    modalAiSpinner.style.display = 'none';
    modalTitleHooks.textContent = language === 'en'
      ? "Error: Gemini API Key is required. Please fill it in the Manual Clip or Auto Clip section."
      : "Error: Gemini API Key belum diisi. Silakan isi di bagian Manual Clip atau Auto Clip.";
    modalAiResult.style.display = 'block';
    return;
  }

  // Ambil konteks spesifik dari momen yang dideteksi
  let clipTitle = '';
  let clipContext = '';
  if (momentIndex !== undefined) {
    const moment = detectedMoments.find(m => m.index === parseInt(momentIndex));
    if (moment) {
      clipTitle = moment.title;
      clipContext = moment.reason;
    }
  }

  try {
    const res = await apiFetch('/generate-copy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, api_key: apiKey, start, end, cookies, clip_title: clipTitle, clip_context: clipContext, language }),
    });
    
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Terjadi kesalahan saat meng-generate copy.');

    parseAndRenderCopy(data);
    
    modalAiSpinner.style.display = 'none';
    modalAiResult.style.display = 'block';
  } catch (err) {
    modalAiSpinner.style.display = 'none';
    modalTitleHooks.textContent = `Error: ${err.message}`;
    modalAiResult.style.display = 'block';
  }
}

function parseAndRenderCopy(data) {
  const language = data.language || (modalAiLanguage ? modalAiLanguage.value : (aiLanguage ? aiLanguage.value : 'id'));
  const title = data.title_hook || data.title || '';
  const caption = data.caption || '';
  const cta = data.cta || '';
  const hashtags = data.hashtags || '';

  modalTitleHooks.textContent = title || '-';
  modalCaption.textContent = caption || '-';
  modalCta.textContent = cta || '-';
  modalHashtags.textContent = hashtags || '-';
}

if (modalBtnCopyAll) {
  modalBtnCopyAll.addEventListener('click', () => {
    const lang = modalAiLanguage ? modalAiLanguage.value : (aiLanguage ? aiLanguage.value : 'id');
    const labels = AI_LABELS[lang] || AI_LABELS.id;
    const sectionTitles = {
      id: { title: 'JUDUL', caption: 'CAPTION', cta: 'CTA', hashtags: 'HASHTAGS' },
      en: { title: 'TITLE', caption: 'CAPTION', cta: 'CTA', hashtags: 'HASHTAGS' }
    };
    const t = sectionTitles[lang] || sectionTitles.id;
    const textToCopy = `[${t.title}]\n${modalTitleHooks.textContent}\n\n[${t.caption}]\n${modalCaption.textContent}\n\n[${t.cta}]\n${modalCta.textContent}\n\n[${t.hashtags}]\n${modalHashtags.textContent}`;
    navigator.clipboard.writeText(textToCopy).then(() => {
      const oldText = modalBtnCopyAll.textContent;
      modalBtnCopyAll.textContent = labels.copied;
      setTimeout(() => modalBtnCopyAll.textContent = oldText, 2000);
    }).catch(err => {
      alert(lang === 'en' ? "Failed to copy text: " + err : "Gagal menyalin teks: " + err);
    });
  });
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}


function formatCopyResponse(data, language) {
  const labels = AI_LABELS[language] || AI_LABELS.id;
  const t = language === 'en'
    ? { title: 'VIDEO TITLE', caption: 'CAPTION', cta: 'CALL TO ACTION', hashtags: 'HASHTAGS' }
    : { title: 'JUDUL VIDEO', caption: 'CAPTION', cta: 'CALL TO ACTION (CTA)', hashtags: 'HASHTAGS' };
  const title = data.title_hook || data.title || '-';
  const caption = data.caption || '-';
  const cta = data.cta || '-';
  const hashtags = data.hashtags || '-';
  return `🌟 **${t.title}**\n${title}\n\n📝 **${t.caption}**\n${caption}\n\n🔥 **${t.cta}**\n${cta}\n\n🏷️ **${t.hashtags}**\n${hashtags}`;
}


// ── Cookies Upload Logic ──────────────────────────────────────────────────
const cookiesFileInput = $('cookies-file-input');
const cookiesFileName = $('cookies-file-name');
const btnUploadCookies = $('btn-upload-cookies');
const cookiesSpinner = $('cookies-spinner');
const cookiesBtnLabel = $('cookies-btn-label');
const cookiesStatus = $('cookies-status');

if (cookiesFileInput) {
  cookiesFileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
      cookiesFileName.textContent = file.name;
    } else {
      cookiesFileName.textContent = "Belum ada file dipilih";
    }
  });
}

if (btnUploadCookies) {
  btnUploadCookies.addEventListener('click', async () => {
    const file = cookiesFileInput.files[0];
    if (!file) {
      alert("Silakan pilih file cookies.txt terlebih dahulu.");
      return;
    }

    const formData = new FormData();
    formData.append('file', file);

    btnUploadCookies.disabled = true;
    cookiesSpinner.style.display = "inline-block";
    cookiesBtnLabel.textContent = "Mengunggah...";

    try {
      const res = await fetch('/upload-cookies', { method: 'POST', body: formData });
      const data = await res.json();
      if (res.ok) {
        cookiesStatus.innerHTML = `<span style="color:var(--accent-success);">✅ Berhasil: ${data.message}</span>`;
        checkCookiesStatus();
      } else {
        cookiesStatus.innerHTML = `<span style="color:var(--accent-danger);">❌ Gagal: ${data.error}</span>`;
      }
    } catch (err) {
      cookiesStatus.innerHTML = `<span style="color:var(--accent-danger);">❌ Error: ${err.message}</span>`;
    } finally {
      btnUploadCookies.disabled = false;
      cookiesSpinner.style.display = "none";
      cookiesBtnLabel.textContent = "⬆️ Upload";
      cookiesFileInput.value = "";
      cookiesFileName.textContent = "Belum ada file dipilih";
    }
  });
}

async function checkCookiesStatus() {
  try {
    const res = await fetch('/cookies-status');
    const data = await res.json();
    if (cookiesStatus) {
      if (data.exists) {
        cookiesStatus.innerHTML = `<span style="color:var(--accent-success);">✅ cookies.txt tersedia di server.</span>`;
      } else {
        cookiesStatus.innerHTML = `<span style="color:var(--text-muted);">❌ cookies.txt belum diupload.</span>`;
      }
    }
  } catch (err) {}
}

window.addEventListener('DOMContentLoaded', checkCookiesStatus);

// ── Cookies Upload Logic ──────────────────────────────────────────────────
async function handleCookiesUpload(e) {
  const file = e.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch('/upload-cookies', { method: 'POST', body: formData });
    const data = await res.json();
    if (res.ok) {
      alert(data.message);
      checkCookiesStatus();
    } else {
      alert(data.error);
    }
  } catch (err) {
    alert("Gagal mengunggah cookies: " + err.message);
  }
}
const manualCookiesFile = $('manual-cookies-file');
if (manualCookiesFile) manualCookiesFile.addEventListener('change', handleCookiesUpload);
const ctrlCookiesFile = $('ctrl-cookies-file');
if (ctrlCookiesFile) ctrlCookiesFile.addEventListener('change', handleCookiesUpload);

async function checkCookiesStatus() {
  try {
    const res = await fetch('/cookies-status');
    const data = await res.json();
    document.querySelectorAll('.cookies-status-text').forEach(el => {
      el.textContent = data.exists ? "✅ cookies.txt tersedia di server." : "❌ cookies.txt belum diupload.";
    });
    if (data.exists) {
      if ($('manual-cookies-toggle')) $('manual-cookies-toggle').checked = true;
      if ($('ctrl-cookies-toggle')) $('ctrl-cookies-toggle').checked = true;
    }
  } catch (err) {}
}
window.addEventListener('DOMContentLoaded', checkCookiesStatus);
