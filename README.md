# 🎓 Sistem Pelacakan Alumni

Aplikasi web berbasis Flask untuk melacak data alumni dari sumber publik di internet menggunakan DuckDuckGo Search dan Grok AI.

---

## 📋 Deskripsi

Sistem ini memungkinkan pengelola institusi untuk:
- Mengimpor data alumni dari file CSV
- Melacak keberadaan alumni di internet (LinkedIn, Instagram, Facebook, TikTok, email, tempat kerja)
- Melihat dan mengekspor hasil pelacakan

Pelacakan dilakukan secara otomatis menggunakan **DuckDuckGo** sebagai search engine publik dan **Grok AI (grok-3-mini)** untuk mengekstrak informasi terstruktur dari hasil pencarian.

---

## 🛠️ Teknologi

| Komponen | Teknologi |
|---|---|
| Backend | Python 3.11, Flask 3.1 |
| Frontend | Bootstrap 5.3, Font Awesome 6.5 |
| Database | SQLite (via Python built-in) |
| Search Engine | DuckDuckGo Search (`duckduckgo-search`) |
| AI Extraction | Grok AI `grok-3-mini` via xAI API |
| Deployment | Render (Gunicorn WSGI) |
| Concurrency | Python `threading.ThreadPoolExecutor` |

---

## ✨ Fitur Utama

- **Import CSV** — Upload dataset alumni dari file CSV apapun, sistem otomatis mendeteksi kolom nama, tahun lulus, dan program studi
- **Pelacakan Otomatis** — Lacak ribuan alumni secara paralel (3 worker concurrent) di background tanpa timeout browser
- **Cari Alumni** — Fuzzy search alumni dari database dengan filter tahun dan program studi
- **Hasil Terpisah** — Hasil pelacakan dikelompokkan: Teridentifikasi, Perlu Verifikasi, Belum Ditemukan
- **Export CSV** — Download hasil pelacakan diurutkan berdasarkan confidence score
- **Live Progress** — Monitor progress pelacakan secara real-time tanpa refresh halaman

---

## 🚀 Cara Menjalankan Lokal

```bash
# Clone repo
git clone https://github.com/achmadrizqy/pelackan-alummni.git
cd pelackan-alummni

# Buat virtual environment
python -m venv venv
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Buat file .env
echo XAI_API_KEY=your_api_key_here > .env

# Jalankan
python app.py
```

Buka browser ke `http://127.0.0.1:5000`

**Default login:** username `user` / password `user`

---

## 📁 Struktur Project

```
├── app.py                  # Main Flask application
├── requirements.txt        # Python dependencies
├── Procfile               # Render/Heroku deployment
├── render.yaml            # Render configuration
├── templates/
│   ├── base.html          # Layout utama (Bootstrap 5)
│   ├── index.html         # Dashboard
│   ├── tracking.html      # Halaman pelacakan
│   ├── cari.html          # Pencarian alumni
│   ├── import_csv.html    # Import dataset CSV
│   └── login.html         # Halaman login
└── static/
    └── css/               # Custom styles
```

---

## 🔄 Alur Penggunaan

```
1. Login → 2. Import CSV → 3. Mulai Pelacakan → 4. Lihat Hasil → 5. Export CSV
```

---

## 🧪 Pengujian Fungsional

Pengujian dilakukan secara manual berdasarkan use case utama sistem.

### UC-01: Import Data Alumni dari CSV

| ID | Skenario | Input | Expected Output | Hasil |
|---|---|---|---|---|
| TC-01.1 | Import CSV valid dengan kolom standar | File CSV dengan kolom "Nama Lulusan", "Tahun Lulus", "Program Studi" | Data terimpor, jumlah alumni tampil di dashboard | ✅ Pass |
| TC-01.2 | Import CSV dengan nama kolom tidak standar | File CSV dengan kolom "name", "year", "major" | Sistem mendeteksi kolom otomatis, data terimpor | ✅ Pass |
| TC-01.3 | Import CSV baru menggantikan data lama | CSV baru setelah ada data sebelumnya | Data alumni lama terhapus, diganti data baru | ✅ Pass |
| TC-01.4 | Import file bukan CSV | File .xlsx atau .txt | Pesan error, upload ditolak | ✅ Pass |
| TC-01.5 | Preview kolom sebelum import | Klik "Preview Kolom" | Menampilkan mapping kolom yang terdeteksi | ✅ Pass |

### UC-02: Pelacakan Alumni Otomatis

| ID | Skenario | Input | Expected Output | Hasil |
|---|---|---|---|---|
| TC-02.1 | Mulai pelacakan dengan data alumni tersedia | Klik "Mulai Pelacakan" | Pelacakan berjalan di background, progress tampil real-time | ✅ Pass |
| TC-02.2 | Alumni sudah pernah dilacak di-skip | Alumni yang namanya sudah ada di hasil pelacakan | Alumni tersebut tidak dilacak ulang | ✅ Pass |
| TC-02.3 | Stop pelacakan di tengah proses | Klik "Stop" saat pelacakan berjalan | Pelacakan berhenti, data yang sudah dilacak tersimpan | ✅ Pass |
| TC-02.4 | Pelacakan paralel 3 worker | Dataset 100 alumni | 3 alumni dilacak bersamaan, hasil tersimpan tanpa konflik | ✅ Pass |
| TC-02.5 | Alumni tidak ditemukan di internet | Nama yang tidak ada di internet publik | Status "Belum Ditemukan", confidence 0% | ✅ Pass |

### UC-03: Pencarian Alumni (Fuzzy Search)

| ID | Skenario | Input | Expected Output | Hasil |
|---|---|---|---|---|
| TC-03.1 | Cari dengan nama lengkap | "Budi Santoso" | Menampilkan alumni dengan similarity score | ✅ Pass |
| TC-03.2 | Cari dengan nama sebagian | "Budi" | Menampilkan semua alumni yang namanya mengandung "Budi" | ✅ Pass |
| TC-03.3 | Cari dengan filter tahun | Nama + tahun lulus | Hasil difilter berdasarkan tahun | ✅ Pass |
| TC-03.4 | Cari nama yang tidak ada | Nama tidak ada di database | Pesan "Tidak Ada Kecocokan" | ✅ Pass |
| TC-03.5 | Database kosong | Cari saat tidak ada alumni | Pesan tidak ada data | ✅ Pass |

### UC-04: Melihat dan Mengekspor Hasil Pelacakan

| ID | Skenario | Input | Expected Output | Hasil |
|---|---|---|---|---|
| TC-04.1 | Lihat hasil terpisah per status | Buka tab hasil | 3 tab: Teridentifikasi, Perlu Verifikasi, Belum Ditemukan | ✅ Pass |
| TC-04.2 | Export CSV hasil pelacakan | Klik "Export CSV" | File CSV terdownload, diurutkan confidence DESC | ✅ Pass |
| TC-04.3 | Reset hasil pelacakan | Klik "Reset" + konfirmasi | Semua hasil terhapus, stat card kembali ke 0 | ✅ Pass |
| TC-04.4 | Export saat tidak ada hasil | Klik export tanpa data | Redirect dengan pesan warning | ✅ Pass |

### UC-05: Autentikasi

| ID | Skenario | Input | Expected Output | Hasil |
|---|---|---|---|---|
| TC-05.1 | Login dengan kredensial benar | username: user, password: user | Login berhasil, redirect ke dashboard | ✅ Pass |
| TC-05.2 | Login dengan kredensial salah | username/password salah | Pesan error, tetap di halaman login | ✅ Pass |
| TC-05.3 | Akses fitur tanpa login | Buka /import atau /tracking tanpa login | Redirect ke halaman login | ✅ Pass |
| TC-05.4 | Logout | Klik Logout | Session dihapus, redirect ke dashboard | ✅ Pass |

---

## 📊 Aspek Kualitas

| Aspek | Implementasi |
|---|---|
| **Correctness** | Pipeline DDG → Regex → Grok AI memastikan data yang diisi berdasarkan bukti nyata dari internet |
| **Reliability** | Retry 2x pada DDG, graceful error handling per alumni, pelacakan tidak berhenti karena 1 error |
| **Performance** | 3 concurrent workers, delay 1.5-3 detik per worker, background thread (tidak timeout browser) |
| **Usability** | Navbar aktif sesuai halaman, flash message untuk setiap aksi, progress real-time |
| **Maintainability** | Kode modular: `_ddg_search`, `_extract_regex`, `_grok_extract`, `cari_data_alumni_grok` terpisah |
| **Security** | Login required untuk aksi write, session management Flask, API key via environment variable |

---

## ⚠️ Catatan

- Hasil pelacakan bergantung pada ketersediaan data alumni di internet publik. Alumni yang tidak memiliki jejak digital tidak akan ditemukan.
- Database SQLite akan reset setiap kali service di-restart di Render (free tier). Gunakan fitur Export CSV untuk menyimpan hasil sebelum restart.
- DuckDuckGo mungkin diblokir di beberapa jaringan institusi/kampus. Gunakan jaringan lain (hotspot) untuk hasil optimal.
- Jangan sleep/hibernate device selama proses pelacakan berlangsung.

---

## 👤 Author

**Achmad Rizqy** — [github.com/achmadrizqy](https://github.com/achmadrizqy)
