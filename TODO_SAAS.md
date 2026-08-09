# TODO — Video Clipper SaaS Roadmap

Dokumen ini merinci fitur-fitur yang perlu dibangun untuk mengubah **Video Clipper** dari aplikasi lokal menjadi **SaaS multi-user** yang siap komersial.

> **Cara pakai:** Setiap item menggunakan checkbox Markdown. Coret item (`[x]`) saat selesai dikerjakan dan telah diverifikasi.

> **Progress Auth P0:** Semua item Auth P0 sudah berjalan kecuali social login.

---

## Prioritas

- **P0 — Must-Have**: Tanpa ini, produk tidak bisa disebut SaaS.
- **P1 — Should-Have**: Dibutuhkan untuk operasional, monetisasi, dan pertumbuhan.
- **P2 — Nice-to-Have**: Differentiator & fitur advanced.
- **P3 — Security & Compliance**: Wajib sebelum ada user berbayar.

---

## P0 — Must-Have (MVP SaaS Foundation)

### Authentication & User Management
- [x] Implementasi register/login dengan email + password
- [ ] Login sosial (Google, GitHub, Apple)
- [x] JWT/session management aman (httpOnly cookie / access + refresh token)
- [x] Forgot password & email verification
- [x] User profile (nama, avatar, timezone, bahasa)
- [x] Password hashing (bcrypt/Argon2)

### Multi-Tenancy & Data Isolation
- [x] Tambahkan `user_id` ke tabel `tasks` dan semua entitas terkait
- [x] Isolasi output file per user (folder atau prefix)
- [x] Isolasi `cookies.txt` per user (encrypted at rest)
- [x] Enkripsi API key Gemini per user
- [x] Middleware authorization agar user hanya akses data sendiri
- [x] Default quota per user (free tier)

### Cloud Storage & CDN
- [ ] Upload hasil clip ke cloud storage (S3 / R2 / Wasabi / MinIO)
- [ ] Generate signed download URL dengan expiry
- [ ] Thumbnail & preview clip via CDN
- [ ] Auto-cleanup file lokal setelah upload sukses
- [ ] Backup metadata clip ke database

### Billing & Subscriptions
- [ ] Definisi pricing plan (Free / Pro / Team / Agency)
- [ ] Integrasi payment gateway (Stripe / Midtrans / Xendit)
- [ ] Webhook untuk status pembayaran & subscription
- [ ] Invoice & receipt otomatis
- [ ] Trial period & downgrade handling
- [ ] Cancel / pause subscription

### Quota & Rate Limiting
- [ ] Tracking usage: menit render, jumlah clip, scan AI, copy AI
- [ ] Quota reset bulanan otomatis
- [ ] Rate limit per endpoint per user
- [ ] Block / queue task saat quota habis
- [ ] Upgrade prompt di UI saat quota hampir habis

---

## P1 — Should-Have (Operational & Growth)

### Admin Dashboard
- [ ] Halaman admin terpisah dengan auth khusus
- [ ] List user dengan filter & search
- [ ] Statistik: user aktif, task harian, revenue, storage usage
- [ ] Monitoring worker queue & server health
- [ ] Ban/suspend user
- [ ] Lihat log error global
- [ ] Manajemen plan & pricing dinamis

### Email & Notifications
- [ ] Email task selesai dengan link download
- [ ] Email quota 80% / 100%
- [ ] Welcome email & onboarding
- [ ] Payment receipt & failed payment reminder
- [ ] Notifikasi in-app (badge/toast)

### Project / Folder / Asset Library
- [ ] User bisa membuat project/folder
- [ ] Organisasi clip per project
- [ ] Upload custom BGM, B-roll, logo, font
- [ ] Save preset subtitle & format favorite
- [ ] Template project reusable

### Webhook & Integrasi
- [ ] Endpoint webhook per user untuk event `clip.done`, `clip.error`
- [ ] Retry policy untuk webhook gagal
- [ ] Secret signature untuk verifikasi webhook

### API for Developers
- [ ] REST API key per user
- [ ] Endpoint programmatic: clip, status, download, delete
- [ ] API documentation (Swagger / Postman)
- [ ] Rate limit khusus API key

---

## P2 — Nice-to-Have (Differentiators)

### Publish & Schedule ke Sosial Media
- [ ] Integrasi TikTok upload
- [ ] Integrasi Instagram Reels (Graph API / third-party)
- [ ] Integrasi YouTube Shorts
- [ ] Integrasi X/Twitter video
- [ ] Scheduling post otomatis
- [ ] Draft queue & approval workflow

### Analytics & Reporting
- [ ] Dashboard usage per user (menit render, clip count, fitur terpakai)
- [ ] Virality score history & trend
- [ ] Track download count per clip
- [ ] Email report mingguan/bulanan

### White-Label / Agency
- [ ] Custom watermark/logo per user
- [ ] Custom subdomain untuk agency
- [ ] Team member invitation (role: owner, editor, viewer)
- [ ] Client approval link
- [ ] Remove default branding untuk plan tertentu

### Mobile & UX
- [ ] Responsive UI untuk mobile
- [ ] Progressive Web App (PWA)
- [ ] Drag-and-drop upload asset
- [ ] Dark mode toggle

### Growth
- [ ] Affiliate / referral program
- [ ] Promo code & discount
- [ ] Waitlist / early access
- [ ] Public landing page

---

## P3 — Security & Compliance

- [ ] Enkripsi API key, cookies, dan data sensitif user di database
- [ ] HTTPS enforcement & HSTS
- [ ] CSRF protection
- [ ] Input validation & sanitasi di semua endpoint
- [ ] Audit log untuk action penting (login, clip, delete, billing change)
- [ ] Data retention policy & auto-delete file setelah X hari
- [ ] GDPR / CCPA compliance: data export & account deletion
- [ ] Security headers (CSP, X-Frame-Options, etc.)
- [ ] Regular dependency scanning

---

## Notes

- Core fitur editing video saat ini sudah kuat; fokus awal adalah **infrastruktur SaaS**, bukan menambah fitur editing baru.
- Setiap task P0 sebaiknya dikerjakan secara bertahap dan diikuti dengan testing.
- Dokumen ini boleh di-update kapan saja sesuai prioritas bisnis atau feedback user.
