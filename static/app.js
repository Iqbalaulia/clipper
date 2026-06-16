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

  try {
    const subtitlePosition = document.querySelector('input[name="subtitle-position"]:checked')?.value || 'bottom';
    const hookTitleValue = $('hook-title') ? $('hook-title').value.trim() : '';
    const hookFontsize = $('hook-fontsize') ? $('hook-fontsize').value : "34";
    const hookPreset = $('hook-style') ? $('hook-style').value : "yellow-pop";
    
    let finalSubType = subtitleType ? subtitleType.value : 'soft';

    const res  = await fetch('/clip', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        url, start, end,
        hook_title:        hookTitleValue,
        hook_fontsize:     hookFontsize,
        hook_preset:       hookPreset,

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
        video_format:      videoFormat    ? videoFormat.value      : 'original',
        cookies:           $('manual-cookies') ? $('manual-cookies').value : '',
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
    tracking:   '🎯 Melacak wajah pembicara...',
    cutting:    '✂️  Memotong video...',
    embedding:  '🔡 Menyisipkan subtitle...',
    processing: '⚙️  Memproses video...',
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

    // Otomatis generate copy jika API key sudah ada
    if (geminiKeyInput && geminiKeyInput.value.trim() !== '') {
      btnGenerateAi.click();
    }
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
    if (!apiKey) return alert('Gemini API Key (di kolom AI Copywriter) wajib diisi untuk fitur ini!');

    // Save key
    localStorage.setItem('clipper_gemini_key', apiKey);

    const oldContent = btnGenerateHook.innerHTML;
    btnGenerateHook.disabled = true;
    btnGenerateHook.innerHTML = '<span class="btn-icon">⏳</span>...';

    try {
      const cookies = $('manual-cookies-toggle') ? $('manual-cookies-toggle').checked : false;

      const res = await fetch('/generate-hook', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, api_key: apiKey, start, end, cookies }),
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

// Load key from storage
const savedKey = localStorage.getItem('clipper_gemini_key');
if (savedKey) geminiKeyInput.value = savedKey;

btnGenerateAi.addEventListener('click', async () => {
  const url = urlInput.value.trim();
  const start = startInput.value.trim();
  const end = endInput.value.trim();
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

  const cookies = $('manual-cookies-toggle') ? $('manual-cookies-toggle').checked : false;

  try {
    const res = await fetch('/generate-copy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, api_key: apiKey, start, end, cookies }),
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
    params: { fontSize: '22', bold: false, italic: false, underline: false, case: 'normal', primaryColor: 'FFFFFF', outlineColor: '000000', backColor: '000000', backAlpha: '80', borderStyle: '3', outlineWidth: '3', shadow: '0' }
  },
  {
    id: 'yellow-box', name: 'Yellow\nBox',
    preview: { text: 'Aa', color: '#FFFF00', shadow: 'none', bg: 'rgba(0,0,0,0.85)', weight: 'bold', style: 'normal', deco: 'none' },
    params: { fontSize: '22', bold: true, italic: false, underline: false, case: 'normal', primaryColor: 'FFFF00', outlineColor: '000000', backColor: '000000', backAlpha: 'CC', borderStyle: '3', outlineWidth: '3', shadow: '0' }
  },
  {
    id: 'black-white-box', name: 'White Box\nBlack Text',
    preview: { text: 'Aa', color: '#000', shadow: 'none', bg: '#fff', weight: 'bold', style: 'normal', deco: 'none' },
    params: { fontSize: '22', bold: true, italic: false, underline: false, case: 'upper', primaryColor: '000000', outlineColor: 'FFFFFF', backColor: 'FFFFFF', backAlpha: '00', borderStyle: '3', outlineWidth: '3', shadow: '0' }
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

// ── Load saved API key ────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  const savedKey = localStorage.getItem('clipper_gemini_key');
  if (savedKey && ctrlApiKey) ctrlApiKey.value = savedKey;
});

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
    if (!apiKey) { alert('Gemini API Key wajib diisi!'); ctrlApiKey.focus(); return; }

    // Save API key
    localStorage.setItem('clipper_gemini_key', apiKey);
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

    try {
      const subtitleLangVal = $('ctrl-subtitle-lang') ? $('ctrl-subtitle-lang').value : 'id,en';
      const res  = await fetch('/detect-moments', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ url, api_key: apiKey, num_moments: numMoments, cookies, subtitle_lang: subtitleLangVal }),
      });
      const data = await res.json();

      if (!res.ok || data.error) {
        showScanStatus('error', '❌ ' + (data.error || 'Terjadi kesalahan.'));
        return;
      }

      detectedMoments = data.moments || [];
      if (detectedMoments.length === 0) {
        showScanStatus('error', '❌ AI tidak dapat menemukan momen. Coba lagi.');
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

    } catch (err) {
      showScanStatus('error', '❌ Gagal menghubungi server: ' + err.message);
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
        <p class="moment-card-title">${escHtml(m.title)}</p>
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

    try {
      const res = await fetch('/clip-moments', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          url:              ctrlVideoUrl,
          moments:          chosenMoments,
          video_format:     videoFormat,
          cookies:          cookies,
          subtitle_enabled:  subtitleEnabled,
          subtitle_lang:     $('ctrl-subtitle-lang') ? $('ctrl-subtitle-lang').value : 'id,en',
          subtitle_type:     'burn',
          subtitle_auto:     true,
          subtitle_position: $('ctrl-sub-position')    ? $('ctrl-sub-position').value    : 'bottom',
          sub_fontsize:      $('ctrl-sub-fontsize')    ? $('ctrl-sub-fontsize').value    : '22',
          sub_case:          $('ctrl-sub-case')        ? $('ctrl-sub-case').value        : 'upper',
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
        }),
      });
      const data = await res.json();

      if (!res.ok || data.error) {
        alert('Error: ' + (data.error || 'Terjadi kesalahan.'));
        btnClipMoments.disabled = false;
        btnClipMoments.classList.remove('loading');
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
      const res  = await fetch('/batch-progress', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ task_ids: taskIds }),
      });
      const data = await res.json();
      const tasks = data.tasks || {};

      let allDone = true;
      let anyError = false;

      batchTaskList.forEach(t => {
        const st = tasks[t.task_id];
        if (!st) return;

        const pct      = st.progress || 0;
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
      });

      // All finished
      if (allDone) {
        clearInterval(batchPollInterval);
        batchPollInterval = null;

        setTimeout(() => {
          batchProgressArea.style.display    = 'none';
          batchDownloadGallery.style.display = 'block';
          renderBatchGallery(batchTaskList);
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
    batchClipsGrid.innerHTML = '<p style="color:var(--accent-danger); font-size:.88rem;">Semua klip gagal diproses.</p>';
    return;
  }

  successTasks.forEach(t => {
    const fileUrl = `/download/${t.output_file}`;
    const card = document.createElement('div');
    card.className = 'batch-clip-card';
    card.innerHTML = `
      <video class="batch-clip-video" src="${fileUrl}" preload="metadata" muted></video>
      <div class="batch-clip-info">
        <p class="batch-clip-title">${escHtml(t.title || `Momen ${t.moment_index}`)}</p>
        <p class="batch-clip-time">⏱ ${t.start} → ${t.end}</p>
        <div style="display: flex; gap: 8px; margin-top: 10px;">
          <a href="${fileUrl}" download class="batch-clip-download" style="flex:1; text-align:center;">⬇️ Download</a>
          <button type="button" class="btn batch-clip-detail-btn" style="flex:1; padding: 6px;" onclick="window.openClipDetailsModal('${fileUrl}', '${t.start}', '${t.end}', ${t.moment_index})">✨ Detail</button>
        </div>
      </div>
    `;
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

window.openClipDetailsModal = async function(fileUrl, start, end, momentIndex) {
  // Reset UI
  clipModal.style.display = 'flex';
  modalVideoPlayer.src = fileUrl;
  modalAiResult.style.display = 'none';
  modalAiSpinner.style.display = 'block';
  modalTitleHooks.innerHTML = '';
  modalCaption.innerHTML = '';
  modalCta.innerHTML = '';
  modalHashtags.innerHTML = '';

  const apiKey = localStorage.getItem('clipper_gemini_key') || (ctrlApiKey ? ctrlApiKey.value : '');
  const url = ctrlVideoUrl || urlInput.value;
  const cookies = ctrlCookiesToggle ? ctrlCookiesToggle.checked : false;

  if (!apiKey) {
    modalAiSpinner.style.display = 'none';
    modalTitleHooks.textContent = "Error: Gemini API Key belum diisi. Silakan isi di bagian Manual Clip atau Auto Clip.";
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
    const res = await fetch('/generate-copy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, api_key: apiKey, start, end, cookies, clip_title: clipTitle, clip_context: clipContext }),
    });
    
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Terjadi kesalahan saat meng-generate copy.');

    parseAndRenderCopy(data.copy);
    
    modalAiSpinner.style.display = 'none';
    modalAiResult.style.display = 'block';
  } catch (err) {
    modalAiSpinner.style.display = 'none';
    modalTitleHooks.textContent = `Error: ${err.message}`;
    modalAiResult.style.display = 'block';
  }
}

function parseAndRenderCopy(markdownText) {
  // Regex untuk memecah respons Llama 3 berdasarkan heading
  const titleMatch = markdownText.match(/JUDUL VIDEO.*?\n([^]*?)(?=\n📝|\n🔥|\n🏷️|$)/i);
  const captionMatch = markdownText.match(/CAPTION.*?\n([^]*?)(?=\n🔥|\n🏷️|$)/i);
  const ctaMatch = markdownText.match(/CALL TO ACTION.*?\n([^]*?)(?=\n🏷️|$)/i);
  const tagsMatch = markdownText.match(/HASHTAGS.*?\n([^]*?)$/i);

  modalTitleHooks.textContent = titleMatch ? titleMatch[1].trim() : '-';
  modalCaption.textContent = captionMatch ? captionMatch[1].trim() : '-';
  modalCta.textContent = ctaMatch ? ctaMatch[1].trim() : '-';
  modalHashtags.textContent = tagsMatch ? tagsMatch[1].trim() : '-';
}

if (modalBtnCopyAll) {
  modalBtnCopyAll.addEventListener('click', () => {
    const textToCopy = `[JUDUL]\n${modalTitleHooks.textContent}\n\n[CAPTION]\n${modalCaption.textContent}\n\n[CTA]\n${modalCta.textContent}\n\n[HASHTAGS]\n${modalHashtags.textContent}`;
    navigator.clipboard.writeText(textToCopy).then(() => {
      const oldText = modalBtnCopyAll.textContent;
      modalBtnCopyAll.textContent = '✅ Berhasil Disalin!';
      setTimeout(() => modalBtnCopyAll.textContent = oldText, 2000);
    }).catch(err => {
      alert("Gagal menyalin teks: " + err);
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
        cookiesStatus.innerHTML = `<span style="color:var(--text-main);">✅ Berhasil: ${data.message}</span>`;
        checkCookiesStatus();
      } else {
        cookiesStatus.innerHTML = `<span style="color:var(--accent-red);">❌ Gagal: ${data.error}</span>`;
      }
    } catch (err) {
      cookiesStatus.innerHTML = `<span style="color:var(--accent-red);">❌ Error: ${err.message}</span>`;
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
        cookiesStatus.innerHTML = `<span style="color:var(--text-main);">✅ Status: cookies.txt sudah tersedia di server. Anda siap memotong video!</span>`;
      } else {
        cookiesStatus.innerHTML = `<span style="color:var(--text-muted);">❌ Status: cookies.txt belum diupload. YouTube mungkin akan memblokir Anda.</span>`;
      }
    }
  } catch (err) {}
}

window.addEventListener('DOMContentLoaded', checkCookiesStatus);
