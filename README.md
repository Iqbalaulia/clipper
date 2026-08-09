# ✂️ Video Clipper

Aplikasi web lokal untuk mengunduh dan memotong video dari YouTube dan 1000+ platform lainnya menggunakan **yt-dlp** dan **FFmpeg**.

## ✨ Fitur Unggulan

- ✂️ **Pemotongan Cepat (Lossless)**: Potong bagian video favorit Anda dengan sangat cepat menggunakan *stream copy* tanpa penurunan kualitas.
- 📱 **Format Video Fleksibel**: Mendukung rasio asli, Vertical 9:16 (Crop Tengah / TikTok style), maupun Vertical dengan Pad Hitam.
- 💬 **Subtitle Otomatis**: Unduh dan sisipkan *soft-sub* atau *hard-sub* (burn-in) langsung ke video.
- 🎨 **Kustomisasi Subtitle ala CapCut**: Tersedia berbagai preset *style* teks profesional (Classic, Bold Pop, Neon Glow, Cinema, Yellow Box, dll) beserta kontrol tata letak dan ukuran.
- 🔥 **Judul Hook (Bait) Dinamis**: Bakar teks pancingan di 4 detik pertama video lengkap dengan gaya teks keren untuk menahan audiens agar *scrolling* berhenti.
- 🤖 **AI Auto Hook Generator**: Biarkan kecerdasan buatan (Groq Llama 3) memikirkan kalimat hook *clickbait* positif terbaik berdasarkan konteks video Anda.
- 📝 **AI Social Media Copywriter**: Secara otomatis menyusun draf *caption* viral, CTA, dan *hashtags* (menggunakan AI) yang disesuaikan spesifik dengan durasi klip video yang Anda potong.
- ⚡ **Real-Time Progress**: Pantau progres *download*, ekstraksi, dan render dengan UI progres modern tanpa perlu *refresh* halaman.

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
│   ├── style.css       # RawBlock brutalist design system
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

## SaaS P0 Configuration

Seluruh integrasi eksternal bersifat opsional saat development. Salin nilai yang
dibutuhkan dari `.env.example` ke environment server.

- Social OAuth callback: `<PUBLIC_BASE_URL>/api/auth/social/<provider>/callback`
- Object storage: isi `S3_*`; mendukung AWS S3, Cloudflare R2, Wasabi, dan MinIO.
- Midtrans: isi `MIDTRANS_SERVER_KEY`, lalu arahkan notification URL ke
  `<PUBLIC_BASE_URL>/api/billing/webhook/midtrans`.
- Production wajib memakai nilai stabil untuk `SECRET_KEY`, `JWT_SECRET_KEY`,
  dan `DATA_ENCRYPTION_KEY`.

Tanpa konfigurasi `S3_*`, asset tetap disimpan secara lokal. Tanpa konfigurasi
OAuth atau Midtrans, tombol provider/checkout terkait dinonaktifkan dengan aman.

## 💡 Tips

- **Stream Copy**: Pemotongan dilakukan tanpa re-encode → sangat cepat, kualitas tetap penuh
- **Multi-Platform**: YouTube, Instagram, TikTok, Twitter, dan 1000+ situs lainnya
- **Output**: Semua hasil tersimpan di folder `outputs/`
