# ✂️ Video Clipper

Aplikasi web lokal untuk mengunduh dan memotong video dari YouTube dan 1000+ platform lainnya menggunakan **yt-dlp** dan **FFmpeg**.

## 🚀 Cara Menjalankan

### 1. Prasyarat

Pastikan sudah terinstall:
- **Python 3.8+** → [python.org](https://python.org)
- **FFmpeg** → [ffmpeg.org](https://ffmpeg.org/download.html) *(harus ada di PATH)*

### 2. Install Dependencies Python

```bash
pip install -r requirements.txt
```

### 3. Jalankan Aplikasi

```bash
python app.py
```

### 4. Buka Browser

Buka **http://localhost:5000**

---

## 📋 Cara Pakai

1. **Isi URL video** — paste URL YouTube / platform lain
2. **Isi Waktu Mulai** — format `HH:MM:SS` atau detik (misal: `90`)
3. **Isi Waktu Selesai** — format `HH:MM:SS` atau detik (misal: `165`)
4. Klik **✂️ Potong Video**
5. Tunggu proses selesai, lalu klik **⬇️ Unduh File**

## 📁 Struktur Proyek

```
ProjectClipper/
├── app.py              # Flask server & routes
├── clipper.py          # Core download & cut logic
├── requirements.txt    # Python dependencies
├── README.md           # Dokumentasi
├── templates/
│   └── index.html      # UI utama
├── static/
│   ├── style.css       # Styling premium dark
│   └── app.js          # Frontend logic
└── outputs/            # Hasil video (auto-dibuat)
```

## ⚙️ Teknologi

| Komponen | Teknologi |
|----------|-----------|
| Backend | Python + Flask |
| Download | yt-dlp |
| Cutting | FFmpeg (stream copy, lossless) |
| Frontend | HTML5 + Vanilla CSS + JS |
| Real-time | Server-Sent Events (SSE) |

## 💡 Tips

- **Stream Copy**: Pemotongan dilakukan tanpa re-encode → sangat cepat, kualitas tetap penuh
- **Multi-Platform**: YouTube, Instagram, TikTok, Twitter, dan 1000+ situs lainnya
- **Output**: Semua hasil tersimpan di folder `outputs/`
