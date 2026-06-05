import re

index_html_path = r"templates\index.html"
with open(index_html_path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace Auto-Clip cookies dropdown
auto_clip_old = """          <div class="field" style="flex: 1; min-width: 140px;">
            <label for="ctrl-cookies">Bypass Blokir (Cookies)</label>
            <div class="input-wrap select-wrap">
              <span class="input-icon">🍪</span>
              <select id="ctrl-cookies">
                <option value="">Tanpa Cookies</option>
                <option value="chrome">Chrome</option>
                <option value="edge">Edge</option>
                <option value="firefox">Firefox</option>
                <option value="brave">Brave</option>
              </select>
            </div>
          </div>"""
auto_clip_new = """          <div class="field" style="flex: 1; min-width: 140px;">
            <label>Bypass Blokir (Cookies)</label>
            <div style="display: flex; gap: 8px; align-items: center; margin-top: 5px;">
              <input type="file" id="ctrl-cookies-file" accept=".txt" style="display:none;" />
              <button type="button" class="btn btn-upload-cookies" style="padding: 8px 12px; font-size: 12px; flex: 1; min-width: 0; background: var(--bg-tertiary);" onclick="document.getElementById('ctrl-cookies-file').click()">Upload cookies.txt</button>
              <label class="checkbox-row" style="margin:0; flex-shrink:0;">
                <input type="checkbox" id="ctrl-cookies-toggle" />
                <span class="checkbox-box"></span>
                <span class="checkbox-label">Aktif</span>
              </label>
            </div>
          </div>"""
content = content.replace(auto_clip_old, auto_clip_new)

# Replace Manual cookies dropdown
manual_old = """            <div class="field" style="flex: 1;">
              <label for="manual-cookies">Bypass Blokir (Cookies)</label>
              <div class="input-wrap select-wrap">
                <span class="input-icon">🍪</span>
                <select id="manual-cookies">
                  <option value="">Tanpa Cookies</option>
                  <option value="chrome">Chrome</option>
                  <option value="edge">Edge</option>
                  <option value="firefox">Firefox</option>
                  <option value="brave">Brave</option>
                </select>
              </div>
              <span class="field-hint">Pilih browser Anda jika YouTube memblokir akses.</span>
            </div>"""
manual_new = """            <div class="field" style="flex: 1;">
              <label>Bypass Blokir (Cookies)</label>
              <div style="display: flex; gap: 8px; align-items: center; margin-top: 5px;">
                <input type="file" id="manual-cookies-file" accept=".txt" style="display:none;" />
                <button type="button" class="btn btn-upload-cookies" style="padding: 10px 12px; font-size: 14px; flex: 1; background: var(--bg-tertiary);" onclick="document.getElementById('manual-cookies-file').click()">Upload cookies.txt</button>
                <label class="checkbox-row" style="margin:0; flex-shrink:0;">
                  <input type="checkbox" id="manual-cookies-toggle" />
                  <span class="checkbox-box"></span>
                  <span class="checkbox-label">Gunakan</span>
                </label>
              </div>
              <span class="field-hint cookies-status-text">Gunakan ekstensi browser "Get cookies.txt LOCALLY"</span>
            </div>"""
content = content.replace(manual_old, manual_new)

with open(index_html_path, "w", encoding="utf-8") as f:
    f.write(content)

# Update app.js
app_js_path = r"static\app.js"
with open(app_js_path, "r", encoding="utf-8") as f:
    js_content = f.read()

# Replace manual cookies variable
js_content = js_content.replace(
    "const cookies       = $('manual-cookies') ? $('manual-cookies').value : '';",
    "const cookies       = $('manual-cookies-toggle') ? $('manual-cookies-toggle').checked : false;"
)
# generate endpoints use manual cookies
js_content = js_content.replace(
    "const cookies = $('manual-cookies') ? $('manual-cookies').value : '';",
    "const cookies = $('manual-cookies-toggle') ? $('manual-cookies-toggle').checked : false;"
)

# Replace auto clip cookies variable
js_content = js_content.replace(
    "const ctrlCookies          = $('ctrl-cookies');",
    "const ctrlCookiesToggle    = $('ctrl-cookies-toggle');"
)
js_content = js_content.replace(
    "const cookies    = ctrlCookies ? ctrlCookies.value : '';",
    "const cookies    = ctrlCookiesToggle ? ctrlCookiesToggle.checked : false;"
)
js_content = js_content.replace(
    "const cookies         = ctrlCookies ? ctrlCookies.value : '';",
    "const cookies         = ctrlCookiesToggle ? ctrlCookiesToggle.checked : false;"
)

# Append cookies upload logic
upload_logic = """
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
"""

js_content += upload_logic

with open(app_js_path, "w", encoding="utf-8") as f:
    f.write(js_content)

print("UI Patched.")
