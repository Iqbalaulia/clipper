# Video Clipper — Feature Documentation

Dokumen ini menjelaskan setiap fitur yang tersedia di **Video Clipper** secara terpisah. Jika Anda mencari dokumentasi arsitektur/teknis, lihat `PROJECT_DOCUMENTATION.md`.

---

## Table of Contents

1. [Manual Clip (Single Clip)](#manual-clip-single-clip)
2. [Auto-Clip AI — Deteksi Momen Kontroversial/Viral](#auto-clip-ai--deteksi-momen-kontroversialviral)
3. [Batch Processing](#batch-processing)
4. [Subtitle: Burn-in & Soft](#subtitle-burn-in--soft)
5. [AI Hook Title Generator](#ai-hook-title-generator)
6. [AI Copywriter](#ai-copywriter)
7. [Background Music (BGM)](#background-music-bgm)
8. [Auto B-Roll Overlay](#auto-b-roll-overlay)
9. [Speaker Tracking (Face Tracking)](#speaker-tracking-face-tracking)
10. [Task Queue & Worker Pool](#task-queue--worker-pool)
11. [Persistent State (SQLite)](#persistent-state-sqlite)
12. [Interactive Timeline Preview](#interactive-timeline-preview)
13. [Cookies Bypass](#cookies-bypass)
14. [Real-Time Progress (SSE)](#real-time-progress-sse)
15. [Video Format Output](#video-format-output)
16. [Multi-Platform Support](#multi-platform-support)
17. [Dependency Check](#dependency-check)
18. [Clip Details Modal](#clip-details-modal)

---

## Manual Clip (Single Clip)

**Deskripsi:**  
Fitur inti untuk memotong satu segmen video dari URL yang diberikan, dengan pengaturan lengkap untuk subtitle, format, hook title, BGM, B-roll, dan lainnya.

**Cara Pakai:**
1. Paste URL video di tab **Manual Clip**.
2. Isi **Start** dan **End** dalam format `HH:MM:SS`, `MM:SS`, atau detik.
3. Atur subtitle (aktifkan, pilih jenis, preset, dsb.).
4. Pilih format output (Original, Vertical 9:16, Speaker Tracking, dsb.).
5. Klik **Generate Clip**.
6. Tunggu progress dan download file hasil.

**Parameter utama:**
- `url`, `start`, `end`
- `subtitle_enabled`, `subtitle_lang`, `subtitle_type` (`soft`/`burn`)
- `subtitle_position`, `sub_fontsize`, `sub_case`, `sub_bold`, `sub_italic`, `sub_underline`
- `subtitle_style` (`standard`/`hormozi`)
- `video_format` (`original`, `vertical-crop`, `vertical-pad`, `vertical-blur`, `vertical-speaker`, `vertical-speaker-blur`)
- `bgm_type` (`none`, `cinematic`, `lofi`, `phonk`)
- `hook_title`, `hook_fontsize`, `hook_preset`, `hook_position`
- `auto_broll`, `transcription_source`, `whisper_model`

**Endpoint:** `POST /clip`  
**File terkait:** `app.py`, `clipper.py`, `task_queue.py`, `static/app.js`

---

## Auto-Clip AI — Deteksi Momen Kontroversial/Viral

**Deskripsi:**  
AI (Gemini) membaca metadata dan transkrip video, lalu menyarankan beberapa momen paling menarik, kontroversial, atau viral berdasarkan konten. Setiap momen dilengkapi judul hook, alasan, dan confidence score.

**Cara Pakai:**
1. Masuk ke tab **Auto-Clip AI**.
2. Paste URL, isi Gemini API Key, dan pilih jumlah momen.
3. Klik **Scan Momen Kontroversial**.
4. Pilih momen yang diinginkan (semua terpilih secara default).
5. Klik **Potong Momen Terpilih**.

**Teknologi:**
- `yt-dlp` untuk mengambil metadata dan subtitle.
- Gemini API dengan prompt khusus untuk deteksi momen viral.
- Sentence-aware boundary snapping agar clip tidak terpotong di tengah kalimat.

**Endpoint:** `POST /detect-moments`  
**File terkait:** `app.py`, `clipper.py`, `static/app.js`

---

## Batch Processing

**Deskripsi:**  
Setelah momen terdeteksi di Auto-Clip, setiap momen bisa diproses sebagai task terpisah secara paralel, dibatasi oleh worker pool. UI menampilkan progress tiap task dan galeri download setelah semua selesai.

**Cara Pakai:**
1. Lakukan scan di Auto-Clip AI.
2. Pilih momen yang ingin diproses.
3. Klik **Potong Momen Terpilih**.
4. Pantau progress batch di panel hasil.
5. Setelah selesai, download satu per satu dari galeri.

**Endpoint:** `POST /clip-moments`, `POST /batch-progress`  
**File terkait:** `app.py`, `clipper.py`, `task_queue.py`, `static/app.js`

---

## Subtitle: Burn-in & Soft

**Deskripsi:**  
Sistem subtitle mendukung dua mode: **soft** (disisipkan sebagai track teks yang bisa diaktifkan/nonaktifkan) dan **burn-in** (dibakar langsung ke video). Burn-in mendukung preset, warna, outline, bayangan, posisi, dan gaya Hormozi.

**Mode:**
- `soft`: subtitle track `mov_text`, bisa dipilih/dimatikan di player.
- `burn`: subtitle digambar langsung ke frame video.

**Gaya:**
- `standard`: subtitle biasa per baris.
- `hormozi`: word-by-word chunking (2-3 kata per layar), huruf kapital, emoji mapping berdasarkan kata kunci.

**Preset UI:** Classic, Bold Pop, Yellow Pop, White Box, Yellow Box, Black White Box, Neon, Cinema, TikTok Red.

**Sumber subtitle:**
1. Subtitle dari video (YouTube auto-caption/manual subtitle).
2. Fallback ke **Whisper** jika subtitle tidak tersedia.

**File terkait:** `clipper.py`, `static/app.js`, `static/style.css`

---

## AI Hook Title Generator

**Deskripsi:**  
Menghasilkan judul hook singkat (maksimal 6 kata, huruf kapital) yang dirancang untuk menarik perhatian di TikTok, Reels, dan Shorts. AI menganalisis judul dan deskripsi video.

**Cara Pakai:**
1. Isi URL di Manual Clip.
2. Isi Gemini API Key di tab AI Copywriter.
3. Klik **✨ Generate Hook** di bagian Hook Title.
4. Hook title akan otomatis terisi.

**Endpoint:** `POST /generate-hook`  
**File terkait:** `app.py`, `static/app.js`

---

## AI Copywriter

**Deskripsi:**  
Menghasilkan draft copywriting viral berdasarkan video dan konteks clip. Output terdiri dari: judul, caption, call-to-action (CTA), dan hashtags. Mendukung bahasa Indonesia dan Inggris.

**Cara Pakai:**
1. Masukkan URL dan API Key.
2. Pilih bahasa (ID/EN).
3. Klik **✨ Generate Copy** di tab AI Copywriter.
4. Hasil bisa disalin ke clipboard.

**Detail Modal:**  
Saat klik **Detail** pada hasil batch clip, sistem mengirim `clip_title` dan `clip_context` dari momen yang terdeteksi sehingga copywriting lebih spesifik.

**Endpoint:** `POST /generate-copy`  
**File terkait:** `app.py`, `static/app.js`

---

## Background Music (BGM)

**Deskripsi:**  
Menambahkan musik latar ke klip dengan mixing otomatis. BGM di-loop dan volume dikecilkan agar tidak menutupi audio utama.

**Pilihan BGM:**
- `cinematic`
- `lofi`
- `phonk`
- `none` (tanpa BGM)

**Cara Pakai:**
Pilih BGM di dropdown sebelum klik **Generate Clip**.

**File terkait:** `clipper.py`, folder `bgm/`

---

## Auto B-Roll Overlay

**Deskripsi:**  
Secara otomatis mendeteksi kata kunci dalam subtitle (misal: uang, waktu, api) dan menimpa video dengan B-roll pendek (2.5 detik) yang relevan pada saat kata tersebut muncul.

**B-Roll yang tersedia:**
- `money.mp4` → uang, duit, cuan, kaya, miliar
- `time.mp4` → waktu, jam, hari, tahun, lama
- `fire.mp4` → panas, api, marah, gila, hancur, terbakar

**Cara Pakai:**
Aktifkan toggle **Auto B-Roll** sebelum memotong. Saat ini hanya bekerja dengan mode burn-in subtitle.

**File terkait:** `clipper.py`, folder `broll/`

---

## Speaker Tracking (Face Tracking)

**Deskripsi:**  
Untuk format vertical 9:16, sistem dapat melacak wajah pembicara secara dinamis sehingga wajah tetap berada di tengah frame. Tersedia dalam dua varian: crop biasa dan crop dengan blur background.

**Format terkait:**
- `vertical-speaker`: crop mengikuti wajah pembicara.
- `vertical-speaker-blur`: speaker tracking + blur background.

**Cara Pakai:**
Pilih format **9:16 Speaker Tracking** atau **9:16 Speaker + Blur** sebelum generate clip.

**Teknologi:**
- BlazeFace TFLite (`blaze_face_short_range.tflite`) via `face_tracker.py`.
- Sampling wajah beberapa fps, smoothing, dan generate crop expression untuk FFmpeg.

**File terkait:** `clipper.py`, `face_tracker.py`, `blaze_face_short_range.tflite`

---

## Task Queue & Worker Pool

**Deskripsi:**  
Semua task clip masuk ke antrian dan diproses oleh worker pool dengan jumlah terbatas (default 2 worker). Ini mencegah server overload ketika banyak user atau task bersamaan.

**Fitur:**
- Task queue bounded (`task_queue.py`).
- Worker thread pool.
- Cancellation support: task yang sedang berjalan bisa dibatalkan, termasuk subprocess `yt-dlp` dan `ffmpeg`.
- Timeout per task (default 1 jam).

**Endpoint:** `POST /cancel/<task_id>`, `GET /queue-status`  
**File terkait:** `task_queue.py`, `runner.py`, `clipper.py`

---

## Persistent State (SQLite)

**Deskripsi:**  
Status task disimpan di SQLite (`data/clipper.db`) sehingga progress tidak hilang meskipun server restart. Task yang sedang berjalan saat server restart akan otomatis ditandai sebagai error.

**Skema:**
- Tabel `tasks`: id, status, progress, output_file, error, params, logs, created_at, updated_at.
- Tabel `task_logs`: log per task.

**File terkait:** `models.py`

---

## Interactive Timeline Preview

**Deskripsi:**  
Timeline interaktif di workspace utama menampilkan durasi video dan wilayah clip yang dipilih. User dapat drag handle Start/End untuk menyesuaikan waktu secara visual.

**Cara Pakai:**
1. Masukkan URL agar metadata video ter-load.
2. Timeline akan menampilkan durasi video.
3. Drag handle kiri (Start) dan kanan (End) untuk memilih segmen.
4. Waktu Start/End otomatis tersinkronisasi dengan input field.

**File terkait:** `static/app.js`, `static/style.css`, `templates/index.html`

---

## Cookies Bypass

**Deskripsi:**  
YouTube sering memblokir akses bot dengan meminta login. User bisa mengunggah file `cookies.txt` dari browser untuk melewati batasan ini. Cookies dapat diupload via file picker atau copy-paste manual.

**Cara Pakai:**
1. Ekspor cookies dari browser menggunakan ekstensi seperti "Get cookies.txt LOCALLY".
2. Upload file `cookies.txt` di bagian **Bypass Blokir**.
3. Atau paste isi cookies di field manual.

**Endpoint:** `GET /cookies-status`, `POST /upload-cookies`  
**File terkait:** `app.py`, `static/app.js`

---

## Real-Time Progress (SSE)

**Deskripsi:**  
Server-Sent Events (SSE) mengirimkan update status, progress, dan log ke browser secara real-time. User melihat progress bar, status teks, dan terminal log berjalan.

**Status yang ditampilkan:**
`pending`, `queued`, `downloading`, `subtitles`, `tracking`, `cutting`, `embedding`, `processing`, `done`, `error`, `cancelling`, `cancelled`.

**Endpoint:** `GET /progress/<task_id>`  
**File terkait:** `app.py`, `static/app.js`

---

## Video Format Output

**Deskripsi:**  
Sistem mendukung berbagai format output untuk berbagai kebutuhan platform.

**Pilihan format:**
- `original`: format asli video, stream-copy jika memungkinkan.
- `vertical-crop`: crop tengah ke 9:16, scale 1080x1920.
- `vertical-pad`: pad black bars ke 9:16, scale 1080x1920.
- `vertical-blur`: crop tengah + blur background, foreground di tengah.
- `vertical-speaker`: speaker tracking crop dinamis.
- `vertical-speaker-blur`: speaker tracking + blur background.

**File terkait:** `clipper.py`, `face_tracker.py`

---

## Multi-Platform Support

**Deskripsi:**  
Karena menggunakan `yt-dlp`, Video Clipper mendukung ratusan platform termasuk YouTube, TikTok, Instagram, Twitter/X, Facebook, dan banyak lagi. Asalkan URL dapat diakses oleh `yt-dlp`, sistem dapat memprosesnya.

**Tips:**
- Untuk YouTube yang diblokir, gunakan cookies bypass.
- Untuk platform tanpa subtitle, aktifkan Whisper.

**File terkait:** `clipper.py`

---

## Dependency Check

**Deskripsi:**  
Saat halaman dibuka, frontend akan mengecek apakah `yt-dlp` dan `ffmpeg` tersedia di server. Status ditampilkan di UI.

**Endpoint:** `GET /check-deps`  
**File terkait:** `app.py`, `static/app.js`

---

## Clip Details Modal

**Deskripsi:**  
Modal detail muncul saat user mengklik tombol **Detail** pada hasil batch clip. Modal ini menampilkan video player dan AI copywriting yang spesifik untuk momen tersebut (menggunakan `clip_title` dan `clip_context`).

**Cara Pakai:**
1. Proses batch clip di Auto-Clip AI.
2. Setelah selesai, klik **Detail** pada salah satu card hasil.
3. Modal akan menampilkan video dan copywriting.
4. Klik **Copy Semua** untuk menyalin copywriting.

**File terkait:** `static/app.js`, `templates/index.html`, `app.py`

---

## Ringkasan File Utama per Fitur

| Fitur | File Utama | Catatan |
|-------|------------|---------|
| Manual Clip | `app.py`, `clipper.py`, `task_queue.py` | Endpoint `/clip` |
| Auto-Clip AI | `app.py`, `clipper.py` | Endpoint `/detect-moments` |
| Batch Processing | `app.py`, `task_queue.py`, `static/app.js` | `/clip-moments`, `/batch-progress` |
| Subtitle | `clipper.py`, `static/app.js` | Burn/soft + Hormozi |
| Hook Title | `app.py` | `/generate-hook` |
| AI Copywriter | `app.py`, `static/app.js` | `/generate-copy` |
| BGM | `clipper.py`, folder `bgm/` | Mixing otomatis |
| B-Roll | `clipper.py`, folder `broll/` | Keyword-based overlay |
| Speaker Tracking | `clipper.py`, `face_tracker.py` | BlazeFace TFLite |
| Task Queue | `task_queue.py`, `runner.py` | Worker pool + cancel |
| Persistent State | `models.py` | SQLite `data/clipper.db` |
| Timeline | `static/app.js`, `static/style.css` | Drag handles |
| Cookies Bypass | `app.py`, `static/app.js` | `/upload-cookies` |
| SSE Progress | `app.py`, `static/app.js` | `/progress/<task_id>` |
| Format Output | `clipper.py` | 6 format |

---

## Catatan Penggunaan

- Semua waktu input menerima format `HH:MM:SS`, `MM:SS`, atau angka detik.
- API Key Gemini digunakan untuk Auto-Clip AI, Hook Title, dan AI Copywriter.
- Whisper bersifat opsional: install `faster-whisper` untuk transkripsi lokal.
- File `cookies.txt` harus diupload ulang jika sudah expired.
- Task yang gagal karena server restart akan otomatis ditandai error saat startup.
