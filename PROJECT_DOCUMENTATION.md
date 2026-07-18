# Dokumentasi Project Clipper Studio

Dokumen ini merupakan penjelasan teknis menyeluruh dari **Project Clipper Studio** — aplikasi web lokal untuk mengunduh, memotong, dan mengedit ulang video dari YouTube (serta 1000+ platform lain) menjadi klip siap publikasi untuk media sosial.

---

## 1. Ikhtisar Proyek

Clipper Studio adalah alat bantu berbasis web yang berjalan di mesin lokal pengguna. Aplikasi ini menggabungkan empat teknologi inti:

- **yt-dlp** — mengunduh metadata dan media dari URL video.
- **FFmpeg** — memotong, mengubah format, membakar subtitle, dan mixing audio.
- **MediaPipe + OpenCV** — mendeteksi wajah untuk fitur *Speaker Tracking*.
- **faster-whisper (opsional)** — transkripsi audio lokal untuk video yang tidak memiliki subtitle YouTube.

Backend ditulis dengan **Python + Flask**, sedangkan frontend dibuat menggunakan **HTML, CSS, dan JavaScript vanilla** tanpa framework frontend eksternal.

Tujuan utama aplikasi adalah membantu kreator konten mempercepat alur kerja *repurpose* video panjang menjadi klip pendek vertikal (9:16) untuk TikTok, Instagram Reels, dan YouTube Shorts.

---

## 2. Fitur Utama

### 2.1 Manual Clip
- Potong video dari URL berdasarkan waktu mulai dan selesai.
- Dukungan format waktu `HH:MM:SS`, `MM:SS`, atau detik mentah.
- Pemotongan cepat tanpa re-encode (*stream copy*) untuk output `original`.

### 2.2 Format Output
| Format | Keterangan |
|--------|------------|
| `original` | Mempertahankan rasio asli video. |
| `vertical-crop` | 9:16, crop center, upscale ke 1080×1920. |
| `vertical-pad` | 9:16 dengan *black bars* di sisi. |
| `vertical-blur` | 9:16 dengan blur background + foreground tengah. |
| `vertical-speaker` | 9:16 dengan *Speaker Tracking* (crop mengikuti wajah). |
| `vertical-speaker-blur` | 9:16 kombinasi Speaker Tracking + blur background. |

### 2.3 Subtitle
- Mengunduh subtitle otomatis (auto-generated) dari YouTube.
- **Fallback Whisper**: jika subtitle YouTube tidak tersedia, sistem dapat menggunakan transkripsi lokal dengan `faster-whisper`.
- Sumber transkripsi dapat dipilih: **YouTube**, **Whisper**, atau **Auto** (YouTube dulu, Whisper fallback).
- Mode **Soft Subtitle** — disisipkan sebagai track teks yang bisa dimatikan.
- Mode **Burn-in** — subtitle dibakar langsung ke video.
- Preset gaya subtitle: *Classic, Bold Pop, Neon Glow, Cinema, Yellow Box, Fire, Breaking, White Box, TikTok*.
- Gaya khusus **Hormozi** — membagi kalimat menjadi 2-3 kata per layar dengan emoji otomatis.
- Kontrol: ukuran font, bold, italic, underline, posisi (bawah/tengah), dan warna.

### 2.4 Hook Title
- Teks pancingan (bait) yang dibakar di awal video (4-6 detik pertama).
- Dukungan preset gaya: *Yellow Pop, TikTok Red, Fire, Breaking, White Box, Neon, Classic*.
- Posisi dapat diatur: atas, tengah, bawah.
- Generator otomatis dengan AI (Gemini).

### 2.5 Background Music (BGM)
- Opsi BGM: `lofi`, `phonk`, `cinematic`.
- Audio BGM dimixing dengan audio asli melalui FFmpeg (`amix`).
- BGM di-*loop* tanpa batas dan dipotong sesuai durasi klip.

### 2.6 Auto B-Roll
- Mendeteksi kata kunci dalam subtitle (mis. "uang", "waktu", "api") dan menyisipkan klip B-Roll pendek secara otomatis.
- Sumber B-Roll berasal dari folder `broll/` (`fire.mp4`, `money.mp4`, `time.mp4`).

### 2.7 Auto-Clip AI
- Menganalisis transkrip video dengan Gemini AI.
- Mendeteksi momen paling viral/kontroversial.
- Setiap momen dilengkapi judul hook, alasan, dan tingkat confidence.
- Mendukung batch processing hingga 10 momen sekaligus.

### 2.8 AI Copywriter
- Menghasilkan draft judul viral, caption, CTA, dan hashtag berdasarkan konten video.
- Mendukung bahasa Indonesia dan Inggris.

### 2.9 Real-Time Progress
- Menggunakan **Server-Sent Events (SSE)** untuk menampilkan log dan progress pemrosesan secara langsung di UI.

### 2.10 Task Queue & Worker Pool
- Setiap task pemotongan dimasukkan ke dalam antrian dengan worker pool terbatas (default 2 worker).
- Mencegah beban CPU/memori meledak saat banyak klip dikirim bersamaan.
- Status task disimpan di SQLite, sehingga bertahan meski server di-restart.

### 2.11 Pembatalan Task & Timeout
- Endpoint `/cancel/<task_id>` untuk membatalkan task yang sedang berjalan.
- Subprocess FFmpeg/yt-dlp yang berjalan dapat dihentikan saat pembatalan.
- Setiap task memiliki batas waktu (timeout) untuk menghindari proses macet.

### 2.12 Logging & Disk Space Check
- Log disimpan ke `logs/clipper.log` selain ditampilkan di terminal.
- Pemeriksaan ruang disk sebelum memulai download/encode.

### 2.13 Interactive Timeline Preview
- Timeline dengan *drag handles* untuk mengatur start/end secara visual.
- Klik pada track akan memindahkan handle terdekat.
- Durasi total video diambil dari metadata dan menskalakan posisi handle.
- Tombol Preview memutar segmen yang dipilih jika video sudah tersedia.

---

## 3. Arsitektur Sistem

```
┌─────────────────┐     HTTP / SSE      ┌────────────────────┐
│  Browser (UI)   │ ◄──────────────────► │  Flask Server      │
│  static/app.js  │                      │  app.py            │
└─────────────────┘                      └──────────┬─────────┘
                                                      │
                          ┌───────────────┬───────────┼──────────┬───────────┐
                          │               │           │          │           │
                          ▼               ▼           ▼          ▼           ▼
                   ┌────────────┐ ┌────────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
                   │   yt-dlp   │ │  task_queue│ │ models │ │  FFmpeg  │ │ MediaPipe│
                   │  download  │ │   worker   │ │SQLiteDB│ │  process │ │  OpenCV  │
                   └────────────┘ └────────────┘ └────────┘ └──────────┘ └──────────┘
                                                      │
                                               ┌────────────┐
                                               │ faster-whisper │
                                               └────────────┘
```

Alur kerja utama:
1. Pengguna memasukkan URL dan pengaturan di browser.
2. Frontend mengirimkan request ke backend Flask.
3. Backend membuat `task_id`, menjalankan proses di *thread* terpisah.
4. Frontend membuat koneksi SSE ke `/progress/<task_id>` untuk menerima update.
5. Backend menjalankan pipeline: download → subtitle → cutting → re-encode → cleanup.
6. File hasil disimpan di `outputs/` dan dapat diunduh.

---

## 4. Struktur File

```
ProjectClipper/
├── app.py                      # Flask server, routes, helper AI
├── clipper.py                  # Pipeline utama: download, cut, encode, subtitle
├── task_queue.py               # Task queue dengan worker pool
├── models.py                   # SQLite persistence layer
├── runner.py                   # Subprocess runner dengan cancellation tracking
├── whisper_engine.py           # Transkripsi lokal dengan faster-whisper (opsional)
├── face_tracker.py             # Face detection & speaker tracking
├── requirements.txt            # Dependensi Python
├── README.md                   # Dokumentasi pengguna
├── design.md                   # Design system (RawBlock Brutalism)
├── app.py                      # Entry point Flask
├── download_bgm.py             # Skrip bantu unduh BGM
├── download_broll.py           # Skrip bantu unduh B-Roll
├── patch_app.py / patch_ui.py  # Skrip patching (utilitas)
├── blaze_face_short_range.tflite  # Model MediaPipe face detection
├── cookies.txt                 # Cookies YouTube untuk bypass bot detection
├── node.exe                    # Runtime Node.js bawaan untuk yt-dlp
├── logs/                       # Log aplikasi
├── data/                       # Database SQLite dan cache Whisper
├── tests/                      # Unit tests
│   └── test_core.py
├── static/
│   ├── style.css               # Styling UI (RawBlock design system)
│   └── app.js                  # Frontend logic, AJAX, SSE, UI state
├── templates/
│   └── index.html              # Halaman utama dashboard
├── outputs/                    # Folder hasil output video & cache
├── bgm/                        # File background music
│   ├── cinematic.mp3
│   ├── lofi.mp3
│   └── phonk.mp3
└── broll/                      # Stok B-Roll pendek
    ├── fire.mp4
    ├── money.mp4
    └── time.mp4
```

---

## 5. Teknologi & Dependensi

### Backend
| Komponen | Library |
|----------|---------|
| Web Framework | Flask |
| Video Download | yt-dlp (dijalankan via `python -m yt_dlp`) |
| Video Processing | FFmpeg (binary eksternal, harus tersedia di PATH) |
| Face Detection | MediaPipe, OpenCV (opencv-python-headless) |
| Transcription (opsional) | faster-whisper |
| Persistence | SQLite3 (stdlib) |
| AI API | Gemini API via requests |
| Task Queue | Python `queue.Queue` + `threading` |

### Frontend
- HTML5 Semantic
- CSS3 Custom Properties (RawBlock Design System)
- JavaScript ES6+ (Fetch API, EventSource, DOM API)

### Requirements (`requirements.txt`)
```
flask
yt-dlp
requests
mediapipe
opencv-python-headless
faster-whisper  # optional
```

### Dependensi Eksternal
- **FFmpeg** — wajib di-install dan tersedia di PATH.
- **Node.js** — runtime bawaan `node.exe` disertakan di folder proyek.

---

## 6. Backend API Routes

### UI & Static
| Route | Method | Fungsi |
|-------|--------|--------|
| `/` | GET | Render halaman utama `index.html`. |
| `/download/<filename>` | GET | Serve file hasil untuk diunduh. |

### Dependencies
| Route | Method | Fungsi |
|-------|--------|--------|
| `/check-deps` | GET | Memeriksa ketersediaan yt-dlp dan FFmpeg. |

### Cookies & Bypass
| Route | Method | Fungsi |
|-------|--------|--------|
| `/cookies-status` | GET | Status keberadaan file `cookies.txt`. |
| `/upload-cookies` | POST | Upload file `cookies.txt` untuk bypass YouTube. |

### Metadata & Info
| Route | Method | Fungsi |
|-------|--------|--------|
| `/video-info` | POST | Mengambil metadata video (judul, durasi, thumbnail, dll). |

### Clip & Processing
| Route | Method | Fungsi |
|-------|--------|--------|
| `/clip` | POST | Memulai task pemotongan manual. |
| `/progress/<task_id>` | GET | SSE stream progress untuk task tertentu. |
| `/cancel/<task_id>` | POST | Membatalkan task yang sedang berjalan. |
| `/queue-status` | GET | Status antrian task queue. |
| `/clip-moments` | POST | Batch clip dari daftar momen AI. |
| `/batch-progress` | POST | Mengambil status batch task. |

### AI Features
| Route | Method | Fungsi |
|-------|--------|--------|
| `/generate-hook` | POST | Generate hook title dengan Gemini AI. |
| `/generate-copy` | POST | Generate caption, CTA, hashtag dengan Gemini AI. |
| `/detect-moments` | POST | Deteksi momen viral/kontroversial dengan AI. |

---

## 7. Pipeline Pemrosesan Video (`clipper.py`)

### 7.1 Task State
Setiap task disimpan dalam SQLite (`data/clipper.db`) dengan struktur:
```python
{
    "status": "pending | queued | downloading | subtitles | tracking | processing | cutting | done | error | cancelling | cancelled",
    "progress": 0,
    "logs": [],
    "output_file": None,
    "error": None,
    "params": {},
}
```

### 7.2 Tahapan Utama (`run_clip`)

1. **Task Queue**
   - `/clip` membuat task di SQLite dan mengirimkannya ke `task_queue`.
   - Worker pool (default 2 worker) memproses task satu per satu sesuai antrian.
   - Subprocess FFmpeg/yt-dlp terdaftar agar dapat dibatalkan lewat `/cancel/<task_id>`.

2. **Download Video**
   - Ekstrak `video_id` dari URL YouTube.
   - Cache video di `_cache_{video_id}.mp4` agar tidak mengunduh ulang.
   - Jika download gagal karena 403, fallback ke format web 360p.
   - Menggunakan `DOWNLOAD_LOCK` global untuk mencegah download bersamaan pada video yang sama.

3. **Download Subtitle & Whisper Fallback**
   - Jika subtitle diaktifkan, unduh subtitle task-specific (`_sub_dl_{task_id}`).
   - Fallback ke file subtitle hasil scan (`_scan_{video_id}`).
   - Jika sumber transkripsi adalah **Auto** atau **Whisper** dan subtitle YouTube tidak tersedia, jalankan transkripsi lokal dengan `faster-whisper`.
   - Hasil Whisper disimpan di `_whisper_{task_id}.srt` dan di-cache berdasarkan MD5 video.

4. **Sentence-Aware Snapping**
   - Jika subtitle tersedia, waktu mulai dan akhir di-*snap* ke batas kalimat penuh.
   - Tujuannya agar klip berisi satu konteks penuh, tidak terpotong di tengah kalimat.

5. **Speaker Tracking** (opsional)
   - Jika format `vertical-speaker` atau `vertical-speaker-blur` dipilih.
   - Memotong segmen 30 detik sebelum target untuk analisis wajah.
   - Menggunakan MediaPipe BlazeFace untuk mendeteksi wajah terbesar.
   - Menghasilkan koordinat crop dinamis dengan EMA smoothing dan *linear interpolation*.
   - Filter FFmpeg dibangun secara otomatis dengan ekspresi `crop=...` berbasis `t` (waktu).

6. **Cutting / Re-encoding**
   - Jika format original dan tanpa burn-in/hook → *stream copy* (cepat).
   - Jika perlu re-encode → *one-pass* dari source video dengan two-stage seek:
     - Fast seek 30 detik sebelum target.
     - Accurate seek untuk presisi frame.

7. **Post-processing Filters**
   - Format 9:16 (crop, pad, blur, speaker tracking, speaker+blur).
   - Burn-in subtitle menggunakan `subtitles` filter dengan `force_style`.
   - Hook title overlay menggunakan file SRT sementara.
   - B-Roll overlay melalui `filter_complex`.
   - BGM mixing melalui `amix`.

8. **Encoding Settings**
   - Video: `libx264`, `-preset fast`, `-crf 22`, `-pix_fmt yuv420p`, profile `high` level 4.0.
   - Audio: `aac`, `-b:a 192k`.
   - `-movflags +faststart` untuk streaming web.

9. **Cleanup**
   - Menghapus file sementara task-specific.
   - Mempertahankan cache video untuk penggunaan berikutnya.

---

## 8. Face Tracking & Speaker Tracking

### 8.1 Analisis Wajah (`face_tracker.analyze_faces`)
- Mengambil sampel frame dengan frekuensi `sample_fps` (default 3 fps).
- Menggunakan model `blaze_face_short_range.tflite`.
- Memilih wajah terbesar dalam setiap frame.
- Mengembalikan koordinat wajah normalisasi (`center_x`, `center_y`, `width`, `height`).

### 8.2 Generate Crop Data (`face_tracker.generate_crop_data`)
- Menghitung dimensi crop untuk rasio 9:16.
- Mengisi frame tanpa wajah dengan interpolasi linear.
- Menerapkan EMA smoothing untuk pergerakan kamera yang halus.

### 8.3 Build Filter String (`face_tracker.build_crop_filter_string`)
- Mereduksi keyframe maksimal ~40 untuk menghindari error FFmpeg.
- Membangun ekspresi piecewise linear berdasarkan variabel `t`.
- Output: string filter `crop=w:h:x_expr:0` yang dapat langsung digunakan FFmpeg.

---

## 9. AI Features Detail

### 9.1 Hook Generator (`/generate-hook`)
- Mengambil metadata video (judul, deskripsi) dengan yt-dlp.
- Mengirim prompt ke Gemini untuk membuat 3 kandidat hook.
- Memilih satu hook paling impactful, membersihkan artefak, dan mengembalikan UPPERCASE.
- Maximal 6 kata.

### 9.2 Copywriter (`/generate-copy`)
- Menggunakan Gemini AI untuk menghasilkan copywriting.
- Gemini mengembalikan JSON dengan field: `title`, `caption`, `cta`, `hashtags`.
- Frontend menampilkan hasil secara terstruktur di modal Detail dan textarea AI Copy.
- Mendukung bahasa Indonesia dan Inggris.
- Bisa menggunakan konteks dari transkrip klip (jika tersedia) atau metadata video.

### 9.3 Whisper Engine (`whisper_engine.py`)
- Modul opsional untuk transkripsi audio lokal menggunakan `faster-whisper`.
- Mendukung model: `tiny`, `base`, `small`, `medium`, `large-v1/v2/v3`.
- Hasil transkripsi di-cache di `data/whisper_cache/` berdasarkan MD5 video.
- Jika `faster-whisper` tidak terinstall, sistem tetap berjalan dengan subtitle YouTube.

### 9.4 Moment Detection (`/detect-moments`)
- Langkah:
  1. Ambil metadata video.
  2. Download subtitle (auto-generated/subtitle resmi) ke `_scan_{video_id}`.
  3. Parse dan format transkrip dengan timestamp `[HH:MM:SS] teks`.
  4. Kirim prompt ke Gemini dengan few-shot examples.
  5. Validasi timestamp dan *snap* ke batas kalimat.
- Output JSON: array `moments` dengan `start`, `end`, `title`, `reason`, `confidence`.

### 9.5 Model Gemini
- Default model candidates: `gemini-2.5-flash`, `gemini-1.5-flash`, `gemini-flash-latest`, `gemini-1.5-pro`.
- Dapat di-override melalui environment variable `GEMINI_MODEL`.
- API version default: `v1beta` (dapat diubah via `GEMINI_API_VERSION`).
- Retry otomatis untuk status 429, 500, 502, 503, 504 dengan exponential backoff.

### 9.6 Interactive Timeline Preview
- Timeline di `workspace-main` menampilkan garis waktu dengan *drag handles*.
- Durasi total diambil dari metadata `/video-info`.
- Drag handle Start/End mengupdate input waktu dan region highlight secara real-time.
- Klik pada track memindahkan handle terdekat.
- Tombol Preview memutar segmen yang dipilih jika video sudah tersedia di workspace player.

---

## 10. Frontend (`static/app.js` & `templates/index.html`)

### 10.1 UI Layout
- Dashboard 3 kolom: **Sidebar kiri**, **Workspace tengah**, **Right panel**.
- Tab navigasi: **Auto-Clip AI**, **Manual Clip**, **Cookies/Bypass**, **Tips**.
- Panel kanan: **Controls**, **Results**, **AI Copy**.

### 10.2 State Preview Area
Workspace memiliki 5 state:
1. `ws-empty` — placeholder awal.
2. `ws-info` — preview metadata video.
3. `ws-processing` — spinner progress.
4. `ws-player` — pemutar hasil.
5. `ws-gallery` — galeri hasil batch.

### 10.3 Interaksi Utama
- Fetch metadata video setelah URL dimasukkan.
- Submit clip manual → SSE ke `/progress/<task_id>`.
- Submit scan AI → `/detect-moments` → tampilkan kartu momen → batch clip.
- AI copywriting modal untuk setiap hasil klip.
- Upload cookies via drag & click.

### 10.4 Design System
- Menggunakan **RawBlock Design System** (lihat `design.md`).
- Ciri khas: kotak tanpa border-radius, border tebal (3-5px), inversi warna hitam-putih, tipografi Archivo Black + Work Sans + Space Mono.

---

## 11. Konfigurasi & Environment Variables

| Variable | Default | Keterangan |
|----------|---------|------------|
| `GEMINI_API_KEY` | — | API key untuk fitur AI (juga diinput via UI). |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Override model Gemini. |
| `GEMINI_API_VERSION` | `v1beta` | Versi API Gemini. |
| `CLIPPER_MAX_WORKERS` | `2` | Jumlah worker concurrent untuk task queue. |
| `CLIPPER_TASK_TIMEOUT` | `3600` | Timeout per task dalam detik. |
| `PATH` | — | Pastikan FFmpeg dan Python tersedia. |

---

## 12. Cara Menjalankan

### Prasyarat
1. Python 3.8 atau lebih baru.
2. FFmpeg terinstall dan tersedia di PATH.

### Install Dependensi
```bash
pip install -r requirements.txt
```

### Jalankan Aplikasi
```bash
python app.py
```

### Buka Browser
```
http://localhost:5000
```

---

## 13. Flow Penggunaan

### Manual Clip
1. Masukkan URL video.
2. Isi waktu mulai dan selesai (`HH:MM:SS` atau detik).
3. Pilih format output.
4. Aktifkan subtitle / hook / BGM jika diperlukan.
5. Klik **Potong Video**.
6. Tunggu progress selesai, lalu unduh hasil.

### Auto-Clip AI
1. Pilih tab **Auto-Clip AI**.
2. Masukkan URL dan Gemini API Key.
3. Pilih jumlah momen, format output, subtitle, B-Roll.
4. Klik **Scan & Deteksi Momen**.
5. Pilih momen yang diinginkan.
6. Klik **Potong Momen Terpilih**.
7. Setelah selesai, unduh setiap klip dari galeri.

---

## 14. Cache & File Sementara

### File Cache (dipertahankan)
- `_cache_{video_id}.mp4` — video hasil download untuk reuse.
- `_scan_{video_id}.srt` — subtitle hasil scan AI untuk reuse.
- `data/whisper_cache/*.json` — hasil transkripsi Whisper untuk reuse.

### File Sementara (dihapus setelah task selesai)
- `_tmpcut_{task_id}.mp4` — hasil potong sementara.
- `_sub_{task_id}.srt` — subtitle setelah di-shift.
- `_hook_{task_id}.srt` — subtitle hook title.
- `_trackcut_{task_id}.mp4` — segmen analisis speaker tracking.
- `_sub_dl_{task_id}.*` — subtitle hasil download task-specific.
- `_whisper_{task_id}.srt` — subtitle hasil transkripsi Whisper.

---

## 15. Batasan & Catatan Penting

1. **YouTube Bot Detection** — YouTube sering memblokir request yt-dlp. Solusi: upload `cookies.txt` yang valid dan segar.
2. **FFmpeg Dependency** — Tanpa FFmpeg, seluruh pipeline pemrosesan gagal.
3. **Speaker Tracking** — Memerlukan wajah yang terlihat jelas; jika tidak terdeteksi, fallback ke center crop.
4. **B-Roll** — Saat ini hanya menyisipkan 1 B-Roll per klip untuk menjaga stabilitas filter complex.
5. **MediaPipe Model** — Model `blaze_face_short_range.tflite` akan diunduh otomatis jika belum ada.
6. **Node.js Runtime** — `node.exe` disertakan untuk menjalankan JS engine yt-dlp; jika dihapus, yt-dlp mungkin mengalami masalah.
7. **Whisper Optional** — `faster-whisper` perlu di-install terpisah (`pip install faster-whisper`). Jika tidak ada, fallback ke subtitle YouTube.

---

## 16. Daftar Endpoint Lengkap

| Endpoint | Method | Payload Kunci | Output |
|----------|--------|---------------|--------|
| `/` | GET | - | Halaman HTML |
| `/check-deps` | GET | - | `{yt_dlp, ffmpeg}` |
| `/cookies-status` | GET | - | `{exists}` |
| `/upload-cookies` | POST | `file` | `{success, message}` |
| `/video-info` | POST | `url` | `{title, channel, duration, duration_str, thumbnail, view_count, like_count, upload_date}` |
| `/clip` | POST | `url`, `start`, `end`, + options | `{task_id}` |
| `/progress/<task_id>` | GET | - | SSE stream |
| `/cancel/<task_id>` | POST | - | `{success, message}` |
| `/queue-status` | GET | - | `{running, queued, max_workers}` |
| `/download/<filename>` | GET | - | File binary |
| `/generate-hook` | POST | `url`, `api_key`, `start`, `end` | `{hook_title}` |
| `/generate-copy` | POST | `url`, `api_key`, `start`, `end`, `language`, `clip_title`, `clip_context` | `{title, language, title_hook, caption, cta, hashtags}` |
| `/detect-moments` | POST | `url`, `api_key`, `num_moments`, `subtitle_lang` | `{moments, video_title, has_transcript, model_used}` |
| `/clip-moments` | POST | `url`, `moments`, + options | `{tasks}` |
| `/batch-progress` | POST | `task_ids` | `{tasks}` |

---

## 17. Kesimpulan

Project Clipper Studio adalah aplikasi desktop/lokal yang menggabungkan otomasi video dengan AI untuk produksi konten media sosial. Arsitekturnya sederhana namun powerful: Flask sebagai orkestrator, yt-dlp sebagai downloader, FFmpeg sebagai mesin pengolahan video, MediaPipe/OpenCV untuk face tracking, dan Gemini AI untuk copywriting & deteksi momen.

Dokumentasi ini dapat digunakan sebagai referensi teknis untuk pengembangan lebih lanjut, onboarding kontributor, atau pemeliharaan sistem.
