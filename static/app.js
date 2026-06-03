/* app.js — Video Clipper Frontend Logic */

const $ = id => document.getElementById(id);

// ── State ────────────────────────────────────────────────────────────────
let currentTaskId = null;
let evtSource     = null;

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

// Hook inputs
const hookToggle      = $('hook-toggle');
const hookStart       = $('hook-start');
const hookEnd         = $('hook-end');
const subBold         = $('sub-bold');
const subItalic       = $('sub-italic');
const subUnderline    = $('sub-underline');
const videoFormat     = $('video-format');
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

// Hidden style preset inputs
const subPrimaryColor = $('sub-primary-color');
const subOutlineColor = $('sub-outline-color');
const subBackColor    = $('sub-back-color');
const subBackAlpha    = $('sub-back-alpha');
const subBorderStyle  = $('sub-border-style');
const subOutlineWidth = $('sub-outline-width');
const subShadowVal    = $('sub-shadow-val');

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

// ── Form Submit ──────────────────────────────────────────────────────────
form.addEventListener('submit', async e => {
  e.preventDefault();

  const url   = urlInput.value.trim();
  const start = startInput.value.trim();
  const end   = endInput.value.trim();

  if (!url || !start || !end) return;

  // Reset UI
  resetUI();
  setLoading(true);
  progressSection.classList.add('visible');
  downloadCard.classList.remove('visible');
  errorCard.classList.remove('visible');
  terminalBody.innerHTML = '';

  try {
    const subtitlePosition = document.querySelector('input[name="subtitle-position"]:checked')?.value || 'bottom';
    
    // Check if hook is enabled, force burn-in for subtitles
    let finalSubType = subtitleType.value;
    if (hookToggle.checked && subtitleToggle.checked) {
      finalSubType = 'burn'; // Force burn-in if hook is used
    }

    const res  = await fetch('/clip', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        url, start, end,
        hook_enabled:      hookToggle.checked,
        hook_start:        hookStart.value,
        hook_end:          hookEnd.value,
        subtitle_enabled:  subtitleToggle.checked,
        subtitle_lang:     subtitleLang.value,
        subtitle_type:     finalSubType,
        subtitle_auto:     subtitleAuto.checked,
        subtitle_position: subtitlePosition,
        sub_fontsize:      subFontsize.value,
        sub_case:          subCase.value,
        sub_bold:          subBold.checked,
        sub_italic:        subItalic.checked,
        sub_underline:     subUnderline.checked,
        video_format:      videoFormat.value,
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
      showError(data.error);
      setLoading(false);
      return;
    }

    currentTaskId = data.task_id;
    startSSE(currentTaskId);

  } catch (err) {
    showError('Gagal menghubungi server: ' + err.message);
    setLoading(false);
  }
});

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
    }
  };
}

function handleUpdate(data) {
  // Progress bar
  const pct = data.progress || 0;
  progressFill.style.width = pct + '%';
  progressPct.textContent  = pct + '%';

  // Status label
  const statusMap = {
    pending:    '⏳ Menunggu...',
    downloading:'⬇️  Mengunduh video...',
    subtitles:  '💬 Memproses subtitle...',
    cutting:    '✂️  Memotong video...',
    embedding:  '🔡 Menyisipkan subtitle...',
    done:       '✅ Selesai!',
    error:      '❌ Error',
  };
  statusLabel.textContent = statusMap[data.status] || data.status;

  // Pulse color
  statusPulse.className = 'pulse';
  if (data.status === 'done')  statusPulse.classList.add('done');
  if (data.status === 'error') statusPulse.classList.add('error');

  // Append new log lines
  if (data.logs && data.logs.length > 0) {
    data.logs.forEach(line => appendLog(line));
  }

  // Done
  if (data.status === 'done' && data.file) {
    evtSource.close();
    setLoading(false);
    showDownload(data.file);
  }

  // Error
  if (data.status === 'error') {
    evtSource.close();
    setLoading(false);
    showError(data.error || 'Terjadi kesalahan.');
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

function showDownload(filename) {
  const fileUrl = `/download/${filename}`;
  downloadLink.href = fileUrl;
  downloadName.textContent = filename;
  
  // Set preview video
  previewVideo.src = fileUrl;
  
  downloadCard.classList.add('visible');
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
  
  // Reset preview video
  previewVideo.pause();
  previewVideo.removeAttribute('src');
  previewVideo.load();
}

// ── "Clip Lagi" button ───────────────────────────────────────────────────
$('btn-new').addEventListener('click', () => {
  progressSection.classList.remove('visible');
  downloadCard.classList.remove('visible');
  errorCard.classList.remove('visible');
  urlInput.value   = '';
  startInput.value = '';
  endInput.value   = '';
  urlInput.focus();
});

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
  });
}

formatTimeInput(startInput);
formatTimeInput(endInput);
formatTimeInput(hookStart);
formatTimeInput(hookEnd);

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

// ── AI Copywriter Logic ──────────────────────────────────────────────────
const geminiKeyInput = $('gemini-key');
const btnGenerateAi  = $('btn-generate-ai');
const aiSpinner      = $('ai-spinner');
const aiBtnLabel     = $('ai-btn-label');
const aiResultWrap   = $('ai-result-wrap');
const aiResult       = $('ai-result');
const btnCopyAi      = $('btn-copy-ai');

// Load key from storage
const savedKey = localStorage.getItem('clipper_gemini_key');
if (savedKey) geminiKeyInput.value = savedKey;

btnGenerateAi.addEventListener('click', async () => {
  const url = urlInput.value.trim();
  const apiKey = geminiKeyInput.value.trim();

  if (!url) return alert('Silakan masukkan URL video terlebih dahulu!');
  if (!apiKey) return alert('Gemini API Key wajib diisi!');

  // Save key
  localStorage.setItem('clipper_gemini_key', apiKey);

  // UI state
  btnGenerateAi.disabled = true;
  aiSpinner.style.display = 'inline-block';
  aiBtnLabel.textContent = 'Menganalisis Video...';
  aiResultWrap.style.display = 'none';
  btnCopyAi.style.display = 'none';

  try {
    const res = await fetch('/generate-copy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, api_key: apiKey }),
    });
    
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Terjadi kesalahan');

    aiResult.value = data.copy;
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
  urlInput.focus();
});

// ── Subtitle Preset Cards ─────────────────────────────────────────────────
const SUBTITLE_PRESETS = [
  {
    id: 'none', name: 'Tanpa\nPreset',
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
    id: 'yellow-pop', name: 'Yellow\nPop',
    preview: { text: 'AA', color: '#FFFF00', shadow: '-2px -2px 0 #000,2px -2px 0 #000,-2px 2px 0 #000,2px 2px 0 #000', bg: 'transparent', weight: '900', style: 'normal', deco: 'none' },
    params: { fontSize: '26', bold: true, italic: false, underline: false, case: 'upper', primaryColor: 'FFFF00', outlineColor: '000000', backColor: '000000', backAlpha: '00', borderStyle: '1', outlineWidth: '3', shadow: '2' }
  },
  {
    id: 'white-box', name: 'White\nBox',
    preview: { text: 'Aa', color: '#fff', shadow: 'none', bg: 'rgba(0,0,0,0.75)', weight: 'normal', style: 'normal', deco: 'none' },
    params: { fontSize: '22', bold: false, italic: false, underline: false, case: 'normal', primaryColor: 'FFFFFF', outlineColor: '000000', backColor: '000000', backAlpha: '80', borderStyle: '3', outlineWidth: '0', shadow: '0' }
  },
  {
    id: 'yellow-box', name: 'Yellow\nBox',
    preview: { text: 'Aa', color: '#FFFF00', shadow: 'none', bg: 'rgba(0,0,0,0.85)', weight: 'bold', style: 'normal', deco: 'none' },
    params: { fontSize: '22', bold: true, italic: false, underline: false, case: 'normal', primaryColor: 'FFFF00', outlineColor: '000000', backColor: '000000', backAlpha: 'CC', borderStyle: '3', outlineWidth: '0', shadow: '0' }
  },
  {
    id: 'neon', name: 'Neon\nGlow',
    preview: { text: 'AA', color: '#00FFFF', shadow: '0 0 6px #00FFFF,0 0 14px #00FFFF', bg: 'transparent', weight: 'bold', style: 'normal', deco: 'none' },
    params: { fontSize: '24', bold: true, italic: false, underline: false, case: 'upper', primaryColor: '00FFFF', outlineColor: '003333', backColor: '000000', backAlpha: '00', borderStyle: '1', outlineWidth: '2', shadow: '5' }
  },
  {
    id: 'cinema', name: 'Cinema',
    preview: { text: 'Aa', color: '#fff', shadow: '2px 2px 10px rgba(0,0,0,0.9)', bg: 'transparent', weight: 'normal', style: 'italic', deco: 'none' },
    params: { fontSize: '20', bold: false, italic: true, underline: false, case: 'normal', primaryColor: 'FFFFFF', outlineColor: '000000', backColor: '000000', backAlpha: '00', borderStyle: '1', outlineWidth: '1', shadow: '5' }
  },
  {
    id: 'tiktok', name: 'TikTok\nRed',
    preview: { text: 'AA', color: '#FF3B5C', shadow: '-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff', bg: 'transparent', weight: '900', style: 'normal', deco: 'none' },
    params: { fontSize: '26', bold: true, italic: false, underline: false, case: 'upper', primaryColor: 'FF3B5C', outlineColor: 'FFFFFF', backColor: '000000', backAlpha: '00', borderStyle: '1', outlineWidth: '2', shadow: '0' }
  },
];

function renderPresetCards() {
  const container = $('preset-cards');
  if (!container) return;
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
      document.querySelectorAll('.preset-card').forEach(c => c.classList.remove('active'));
      card.classList.add('active');
      applyPreset(preset);
    });

    container.appendChild(card);
  });
}

function applyPreset(preset) {
  if (!preset.params) return; // "Tanpa Preset" — don't change anything
  const p = preset.params;

  // Size + case
  if (subFontsize)   subFontsize.value   = p.fontSize;
  if (subCase)       subCase.value       = p.case;

  // Checkboxes
  if (subBold)       subBold.checked     = p.bold;
  if (subItalic)     subItalic.checked   = p.italic;
  if (subUnderline)  subUnderline.checked = p.underline;

  // Hidden color/style params
  if (subPrimaryColor) subPrimaryColor.value = p.primaryColor;
  if (subOutlineColor) subOutlineColor.value = p.outlineColor;
  if (subBackColor)    subBackColor.value    = p.backColor;
  if (subBackAlpha)    subBackAlpha.value    = p.backAlpha;
  if (subBorderStyle)  subBorderStyle.value  = p.borderStyle;
  if (subOutlineWidth) subOutlineWidth.value = p.outlineWidth;
  if (subShadowVal)    subShadowVal.value    = p.shadow;
}
