from flask import Flask, render_template, request, redirect, session, flash, jsonify
from functools import wraps
import sqlite3
import os
import json
import csv
import io
import re
import time
import random
import threading
from openai import OpenAI
from dotenv import load_dotenv
from duckduckgo_search import DDGS

load_dotenv()

app = Flask(__name__)
app.secret_key = 'super_secret_key_alumni_tracking'

# Grok AI client
xai_client = OpenAI(
    api_key=os.getenv("XAI_API_KEY"),
    base_url="https://api.x.ai/v1"
)

# ── State pelacakan background ──────────────────────────────────────────────
_tracking_state = {
    "running": False,
    "total": 0,
    "processed": 0,
    "berhasil": 0,
    "dilewati": 0,
    "current": "",
    "log": [],          # list pesan singkat
    "done": False,
}
_tracking_lock = threading.Lock()

def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS alumni (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nama TEXT,
        tahun TEXT,
        prodi TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS hasil_pelacakan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nama TEXT,
        linkedin TEXT,
        instagram TEXT,
        facebook TEXT,
        tiktok TEXT,
        email TEXT,
        no_hp TEXT,
        tempat_bekerja TEXT,
        alamat_bekerja TEXT,
        posisi TEXT,
        jenis_pekerjaan TEXT,
        sosmed_perusahaan TEXT,
        confidence REAL,
        status TEXT,
        sumber TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT
    )
    """)

    cur.execute("SELECT * FROM users WHERE username='user'")
    if not cur.fetchone():
        cur.execute("INSERT INTO users (username, password) VALUES ('user', 'user')")

    conn.commit()
    conn.close()


# ===============================
# AUTHENTICATION DECORATOR
# ===============================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Silakan login terlebih dahulu untuk mengakses fitur ini.', 'warning')
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function


# ===============================
# PENCARIAN DATA ALUMNI
# Strategi: DDG-first, Grok hanya jika DDG dapat data
# Hemat token: ekstrak regex dulu, Grok hanya untuk sisa
# ===============================

# ── Regex extractors (zero token cost) ──────────────────────────────────────

_RE_EMAIL   = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')
_RE_PHONE   = re.compile(r'(?<!\d)(?:\+62|62|0)8[1-9][0-9]{6,10}(?!\d)')
_RE_LI      = re.compile(r'linkedin\.com/in/([\w\-]{3,80})', re.I)
_RE_IG      = re.compile(r'instagram\.com/([\w\.]{3,50})/?(?:\s|$|")', re.I)
_RE_FB      = re.compile(r'facebook\.com/([\w\.]{3,80})/?(?:\?|$|\s|")', re.I)
_RE_TT      = re.compile(r'tiktok\.com/@([\w\.]{3,50})/?', re.I)

_FB_SKIP    = {'groups', 'pages', 'events', 'marketplace', 'help', 'login',
               'photo', 'video', 'watch', 'share', 'sharer', 'home', 'profile.php'}
_IG_SKIP    = {'p', 'reel', 'stories', 'explore', 'accounts', 'tv', 'reels'}

_PNS_KW     = ['pns','pegawai negeri','aparatur sipil','asn','kementerian',
               'dinas ','pemkot','pemkab','pemda','bumn','polri','tni ',
               'kodam','puskesmas','rsud','kelurahan','kecamatan','ditjen']
_WIRA_KW    = ['owner','ceo','founder','co-founder','entrepreneur','wirausaha',
               'pengusaha','self-employed','freelance','wiraswasta','direktur utama']


def _ddg_search(query: str, max_results: int = 5) -> list:
    """DuckDuckGo search dengan retry dan fallback."""
    for attempt in range(2):
        try:
            with DDGS(timeout=15) as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return results
        except Exception as e:
            err = str(e).lower()
            # Kalau diblokir/timeout jaringan, tidak perlu retry
            if 'timeout' in err or 'connect' in err or 'blocked' in err:
                return []
            time.sleep(2 + attempt * 2)
    return []


def _extract_regex(text: str) -> dict:
    """Ekstrak data dari teks mentah tanpa AI — zero token."""
    out = {}

    m = _RE_LI.search(text)
    if m:
        out['linkedin'] = f"https://www.linkedin.com/in/{m.group(1)}"

    m = _RE_IG.search(text)
    if m and m.group(1).lower() not in _IG_SKIP:
        out['instagram'] = f"https://www.instagram.com/{m.group(1)}"

    m = _RE_FB.search(text)
    if m:
        slug = m.group(1)
        if slug.lower() not in _FB_SKIP:
            out['facebook'] = f"https://www.facebook.com/{slug}"

    m = _RE_TT.search(text)
    if m:
        out['tiktok'] = f"https://www.tiktok.com/@{m.group(1)}"

    m = _RE_EMAIL.search(text)
    if m and 'noreply' not in m.group(0).lower():
        out['email'] = m.group(0).lower()

    clean = re.sub(r'[\s\-\.]', '', text)
    m = _RE_PHONE.search(clean)
    if m:
        raw = m.group(0)
        out['no_hp'] = ('+62' + raw[1:]) if raw.startswith('0') else ('+' + raw if raw.startswith('62') else raw)

    tl = text.lower()
    if any(k in tl for k in _PNS_KW):
        out['jenis_pekerjaan'] = 'PNS'
    elif any(k in tl for k in _WIRA_KW):
        out['jenis_pekerjaan'] = 'Wirausaha'

    return out


def _grok_extract(nama: str, prodi: str, tahun: str, snippets: list) -> dict:
    """
    Panggil Grok untuk ekstrak data dari snippets DDG.
    Jika snippets kosong, skip — tidak ada gunanya tebak-tebakan.
    """
    if not snippets:
        return {}

    ctx = "\n".join(s[:200] for s in snippets[:5])
    prompt_json = (
        f'Alumni Indonesia: {nama}, prodi {prodi}, lulus {tahun}.\n'
        f'Data web:\n{ctx}\n\n'
        f'Ekstrak info alumni ini dari teks di atas. '
        f'HANYA isi jika ada bukti nyata di teks. Kosongkan jika tidak ada.\n'
        f'Kembalikan HANYA JSON:\n'
        f'{{"linkedin":"","instagram":"","facebook":"","tiktok":"",'
        f'"email":"","no_hp":"","tempat_bekerja":"","posisi":"",'
        f'"jenis_pekerjaan":"","alamat_bekerja":""}}'
    )

    try:
        resp = xai_client.chat.completions.create(
            model="grok-3-mini",
            messages=[
                {"role": "system", "content": "Kembalikan JSON saja tanpa teks lain."},
                {"role": "user",   "content": prompt_json}
            ],
            max_tokens=220,
            temperature=0
        )
        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            raw = m.group(0)
        data = json.loads(raw)
        cleaned = {}
        for k, v in data.items():
            if v and str(v).strip().lower() not in ('', 'null', 'none', 'n/a', '-', 'tidak ada', 'kosong'):
                cleaned[k] = str(v).strip()
        return cleaned
    except Exception:
        return {}


def cari_data_alumni_grok(nama: str, prodi: str, tahun: str) -> dict:
    """
    Pipeline pencarian:
    1. DDG query 1: nama + prodi + sosmed/pekerjaan
    2. DDG query 2: nama tanpa tanda kutip (lebih luas) jika query 1 kosong
    3. Grok dipanggil HANYA jika DDG dapat snippet
    4. Confidence & status dihitung dari hasil nyata
    """
    result = {
        "linkedin": "", "instagram": "", "facebook": "", "tiktok": "",
        "email": "", "no_hp": "", "tempat_bekerja": "", "alamat_bekerja": "",
        "posisi": "", "jenis_pekerjaan": "", "sosmed_perusahaan": "",
        "confidence": 0.0, "status": "Belum Ditemukan",
        "sumber": "DDG"
    }

    all_text = ""
    snippets  = []

    # ── Query 1: spesifik dengan tanda kutip ────────────────────────────────
    q1 = f'"{nama}" {prodi} bekerja OR linkedin OR jabatan OR perusahaan'
    r1 = _ddg_search(q1, max_results=5)
    for r in r1:
        t = f"{r.get('title','')} {r.get('body','')}"
        all_text += " " + t
        snippets.append(t[:300])

    time.sleep(random.uniform(0.8, 1.5))

    # ── Query 2: tanpa kutip, lebih luas ────────────────────────────────────
    if len(snippets) < 2:
        q2 = f'{nama} {prodi} {tahun} alumni Indonesia'
        r2 = _ddg_search(q2, max_results=4)
        for r in r2:
            t = f"{r.get('title','')} {r.get('body','')}"
            all_text += " " + t
            snippets.append(t[:300])
        time.sleep(random.uniform(0.8, 1.5))

    # ── Regex extract ────────────────────────────────────────────────────────
    if all_text:
        found = _extract_regex(all_text)
        for k, v in found.items():
            if v:
                result[k] = v

    # ── Grok hanya jika ada snippet ─────────────────────────────────────────
    if snippets:
        result["sumber"] = "DDG + Grok AI"
        grok_data = _grok_extract(nama, prodi, tahun, snippets)
        for k, v in grok_data.items():
            if v and not result.get(k):
                result[k] = v
    else:
        result["sumber"] = "Tidak ditemukan"

    # ── Default jenis_pekerjaan ──────────────────────────────────────────────
    if not result.get("jenis_pekerjaan"):
        if result.get("tempat_bekerja") or result.get("posisi"):
            result["jenis_pekerjaan"] = "Swasta"

    # ── Confidence & status ──────────────────────────────────────────────────
    fields = ["linkedin", "tempat_bekerja", "posisi", "jenis_pekerjaan", "email"]
    terisi = sum(1 for f in fields if str(result.get(f, "")).strip())
    conf   = round(terisi / len(fields), 2)
    result["confidence"] = conf
    result["status"] = (
        "Teridentifikasi"  if conf >= 0.6 else
        "Perlu Verifikasi" if conf >= 0.2 else
        "Belum Ditemukan"
    )

    return result


def _empty_result(pesan=""):
    return {
        "linkedin": "", "instagram": "", "facebook": "", "tiktok": "",
        "email": "", "no_hp": "", "tempat_bekerja": pesan, "alamat_bekerja": "",
        "posisi": "", "jenis_pekerjaan": "", "sosmed_perusahaan": "",
        "confidence": 0.0, "status": "Belum Ditemukan", "sumber": "Error"
    }



# ===============================
# DASHBOARD (SINGLE PAGE - TANPA LOGIN)
# ===============================

@app.route("/")
def index():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM alumni")
    alumni = cur.fetchall()
    cur.execute("SELECT * FROM hasil_pelacakan")
    hasil = cur.fetchall()
    conn.close()

    # Hitung statistik
    total = len(alumni)
    teridentifikasi = sum(1 for h in hasil if h['status'] == 'Teridentifikasi')
    perlu_verifikasi = sum(1 for h in hasil if h['status'] == 'Perlu Verifikasi')
    belum_ditemukan = sum(1 for h in hasil if h['status'] == 'Belum Ditemukan')

    return render_template("index.html", alumni=alumni, hasil=hasil,
                           total=total, teridentifikasi=teridentifikasi,
                           perlu_verifikasi=perlu_verifikasi, belum_ditemukan=belum_ditemukan)


# ===============================
# AUTHENTICATION ROUTES
# ===============================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
        user = cur.fetchone()
        conn.close()

        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash('Login berhasil!', 'success')
            return redirect("/")
        else:
            flash('Username atau password salah.', 'danger')

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash('Anda telah logout.', 'info')
    return redirect("/")


# ===============================
# TAMBAH ALUMNI (PERLU LOGIN)
# ===============================

@app.route("/tambah_alumni", methods=["POST"])
@login_required
def tambah_alumni():
    nama = request.form["nama"]
    tahun = request.form["tahun"]
    prodi = request.form["prodi"]

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO alumni (nama,tahun,prodi) VALUES (?,?,?)",
        (nama, tahun, prodi)
    )
    conn.commit()
    conn.close()
    flash('Alumni berhasil ditambahkan!', 'success')
    return redirect("/")


# ===============================
# HAPUS ALUMNI (PERLU LOGIN)
# ===============================

@app.route("/hapus_alumni/<int:id>")
@login_required
def hapus_alumni(id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM alumni WHERE id=?", (id,))
    conn.commit()
    conn.close()
    flash('Alumni berhasil dihapus.', 'info')
    return redirect("/")


@app.route("/hapus_semua_alumni")
@login_required
def hapus_semua_alumni():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM alumni")
    conn.commit()
    conn.close()
    flash('Semua data alumni berhasil dihapus.', 'warning')
    return redirect("/")


# ===============================
# MENJALANKAN PROSES PELACAKAN (PERLU LOGIN)
# Background thread — tidak timeout browser
# ===============================

def _run_pelacakan_background(alumni_list):
    """
    Pelacakan paralel dengan ThreadPoolExecutor.
    - 3 worker concurrent (aman untuk DDG rate limit)
    - Setiap worker punya koneksi DB sendiri
    - Delay per-worker 1.5-3 detik (lebih cepat dari sequential 2-4 detik)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    NUM_WORKERS = 3  # sweet spot: cepat tapi tidak trigger block DDG

    def _lacak_satu(a):
        """Task untuk satu alumni — dijalankan di worker thread."""
        with _tracking_lock:
            if not _tracking_state["running"]:
                return None  # stop signal
            _tracking_state["current"] = a["nama"]

        try:
            data = cari_data_alumni_grok(a["nama"], a["prodi"] or "", a["tahun"] or "")

            # Setiap thread buka koneksi DB sendiri (thread-safe)
            conn = sqlite3.connect("database.db")
            try:
                conn.execute("""
                    INSERT INTO hasil_pelacakan
                    (nama, linkedin, instagram, facebook, tiktok, email, no_hp,
                     tempat_bekerja, alamat_bekerja, posisi, jenis_pekerjaan,
                     sosmed_perusahaan, confidence, status, sumber)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    a["nama"],
                    data["linkedin"], data["instagram"], data["facebook"], data["tiktok"],
                    data["email"], data["no_hp"],
                    data["tempat_bekerja"], data["alamat_bekerja"],
                    data["posisi"], data["jenis_pekerjaan"],
                    data["sosmed_perusahaan"],
                    data["confidence"], data["status"], data["sumber"]
                ))
                conn.commit()
            finally:
                conn.close()

            with _tracking_lock:
                _tracking_state["berhasil"]  += 1
                _tracking_state["processed"] += 1
                _tracking_state["log"].append(
                    f"✓ {a['nama']} → {data['status']} ({data['sumber']})"
                )
                if len(_tracking_state["log"]) > 200:
                    _tracking_state["log"] = _tracking_state["log"][-100:]

            # Delay per-worker lebih pendek karena paralel
            time.sleep(random.uniform(1.5, 3.0))
            return data

        except Exception as e:
            with _tracking_lock:
                _tracking_state["processed"] += 1
                _tracking_state["log"].append(f"✗ {a['nama']} → {str(e)[:60]}")
            time.sleep(1.0)
            return None

    try:
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            futures = {executor.submit(_lacak_satu, a): a for a in alumni_list}
            for future in as_completed(futures):
                # Cek stop signal
                with _tracking_lock:
                    if not _tracking_state["running"]:
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
                try:
                    future.result()
                except Exception:
                    pass
    finally:
        with _tracking_lock:
            _tracking_state["running"] = False
            _tracking_state["done"]    = True
            _tracking_state["current"] = ""



@app.route("/jalankan_pelacakan")
@login_required
def jalankan_pelacakan():
    return _mulai_pelacakan(update_mode=False)

@app.route("/lacak_ulang")
@login_required
def lacak_ulang():
    """Lacak ulang SEMUA alumni, replace hasil lama."""
    return _mulai_pelacakan(update_mode=True)

def _mulai_pelacakan(update_mode=False):
    global _tracking_state

    with _tracking_lock:
        if _tracking_state["running"]:
            flash("Pelacakan sedang berjalan di background.", "info")
            return redirect("/tracking")

    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM alumni")
    semua = cur.fetchall()

    if not semua:
        flash("Tidak ada data alumni.", "warning")
        conn.close()
        return redirect("/")

    if update_mode:
        # Hapus semua hasil lama, lacak ulang semua
        cur.execute("DELETE FROM hasil_pelacakan")
        conn.commit()
        antrian = list(semua)
    else:
        # Hanya lacak yang belum pernah dilacak
        cur.execute("SELECT DISTINCT nama FROM hasil_pelacakan")
        sudah = {r["nama"].lower() for r in cur.fetchall()}
        antrian = [a for a in semua if a["nama"].lower() not in sudah]

    conn.close()

    if not antrian:
        flash("Semua alumni sudah pernah dilacak. Gunakan 'Lacak Ulang' untuk memperbarui.", "info")
        return redirect("/")

    with _tracking_lock:
        _tracking_state.update({
            "running":   True,
            "done":      False,
            "total":     len(antrian),
            "processed": 0,
            "berhasil":  0,
            "dilewati":  len(semua) - len(antrian),
            "current":   "",
            "log":       [f"{'[UPDATE] ' if update_mode else ''}Memulai pelacakan {len(antrian)} alumni..."],
        })

    t = threading.Thread(target=_run_pelacakan_background, args=(antrian,), daemon=True)
    t.start()

    mode_txt = "ulang (replace data lama)" if update_mode else "baru"
    flash(f"Pelacakan {mode_txt} dimulai untuk {len(antrian)} alumni.", "success")
    return redirect("/tracking")


@app.route("/api/tracking_status")
def tracking_status():
    with _tracking_lock:
        state = dict(_tracking_state)
        state["log"] = state["log"][-20:]  # kirim 20 log terakhir saja
    return jsonify(state)


@app.route("/stop_pelacakan")
@login_required
def stop_pelacakan():
    with _tracking_lock:
        _tracking_state["running"] = False
    flash("Pelacakan dihentikan.", "warning")
    return redirect("/tracking")



# ===============================
# RESET HASIL PELACAKAN (PERLU LOGIN)
# ===============================

@app.route("/reset_hasil")
@login_required
def reset_hasil():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM hasil_pelacakan")
    conn.commit()
    conn.close()
    flash('Hasil pelacakan berhasil direset.', 'info')
    return redirect("/")


# ===============================
# EXPORT CSV HASIL PELACAKAN
# ===============================

@app.route("/export_csv")
@login_required
def export_csv():
    import io as _io
    from flask import Response

    conn = get_db()
    cur  = conn.cursor()
    # Urutkan: confidence DESC, lalu status, lalu nama
    cur.execute("""
        SELECT * FROM hasil_pelacakan
        ORDER BY confidence DESC, 
                 CASE status 
                     WHEN 'Teridentifikasi' THEN 1 
                     WHEN 'Perlu Verifikasi' THEN 2 
                     ELSE 3 
                 END,
                 nama
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        flash("Tidak ada data untuk diekspor.", "warning")
        return redirect("/")

    output = _io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "No", "Nama", "Status", "Confidence (%)",
        "LinkedIn", "Instagram", "Facebook", "TikTok",
        "Email", "No HP",
        "Tempat Bekerja", "Alamat Bekerja", "Posisi", "Jenis Pekerjaan",
        "Sosmed Perusahaan", "Sumber"
    ])

    for i, r in enumerate(rows, 1):
        writer.writerow([
            i, r["nama"], r["status"],
            f"{round((r['confidence'] or 0) * 100)}%",
            r["linkedin"] or "", r["instagram"] or "",
            r["facebook"] or "", r["tiktok"] or "",
            r["email"] or "", r["no_hp"] or "",
            r["tempat_bekerja"] or "", r["alamat_bekerja"] or "",
            r["posisi"] or "", r["jenis_pekerjaan"] or "",
            r["sosmed_perusahaan"] or "", r["sumber"] or "",
        ])

    from datetime import datetime
    filename = f"hasil_pelacakan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# ===============================
# HALAMAN CARI ALUMNI
# ===============================

@app.route("/cari")
def cari():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM alumni")
    row = cur.fetchone()
    conn.close()
    total_alumni = row['total'] if row else 0
    return render_template("cari.html", total_alumni=total_alumni)

@app.route("/cari_alumni_dari_csv", methods=["POST"])
def cari_alumni_dari_csv():
    from difflib import SequenceMatcher
    data = request.get_json()
    nama_cari = data.get("nama", "").strip().lower()
    tahun = data.get("tahun", "")
    prodi = data.get("prodi", "")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM alumni")
    semua = cur.fetchall()
    conn.close()

    matches = []
    for a in semua:
        ratio = SequenceMatcher(None, nama_cari, a['nama'].lower()).ratio()
        if tahun and str(a['tahun']) != str(tahun):
            ratio *= 0.8
        if prodi and prodi.lower() not in (a['prodi'] or '').lower():
            ratio *= 0.9
        if ratio >= 0.4:
            matches.append({
                "nama": a['nama'],
                "nim": "",
                "tahun_masuk": a['tahun'],
                "tanggal_lulus": "",
                "fakultas": "",
                "program_studi": a['prodi'],
                "similarity": round(ratio * 100, 1)
            })

    matches.sort(key=lambda x: x['similarity'], reverse=True)
    return jsonify({"status": "success", "matches": matches[:10]})


# ===============================
# HALAMAN IMPORT CSV
# ===============================

@app.route("/import")
@login_required
def import_csv_page():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM alumni")
    row = cur.fetchone()
    conn.close()
    total_alumni = row['total'] if row else 0
    return render_template("import_csv.html", total_alumni=total_alumni)

def _detect_columns(headers):
    """
    Deteksi kolom CSV secara fleksibel dengan prioritas:
    - Tahun lulus/kelulusan lebih diprioritaskan dari tahun masuk/angkatan
    - Nama kolom dicocokkan case-insensitive
    """
    col_nama = col_tahun = col_prodi = col_nim = col_fakultas = None

    # Normalisasi headers
    norm = [(h, h.lower().strip().replace('-', ' ').replace('_', ' ')) for h in headers]

    # --- NAMA ---
    for h, hl in norm:
        if not col_nama and any(k in hl for k in ['nama lulusan', 'nama alumni', 'nama mahasiswa', 'nama lengkap']):
            col_nama = h; break
    if not col_nama:
        for h, hl in norm:
            if not col_nama and any(k == hl for k in ['nama', 'name']):
                col_nama = h; break
    if not col_nama:
        for h, hl in norm:
            if not col_nama and any(k in hl for k in ['nama', 'name']):
                col_nama = h; break

    # --- TAHUN (prioritas: lulus > wisuda > kelulusan > masuk > angkatan > tahun) ---
    for h, hl in norm:
        if not col_tahun and any(k in hl for k in ['tahun lulus', 'thn lulus', 'year grad', 'tahun kelulusan', 'tgl lulus', 'tanggal lulus', 'wisuda']):
            col_tahun = h; break
    if not col_tahun:
        for h, hl in norm:
            if not col_tahun and 'lulus' in hl:
                col_tahun = h; break
    if not col_tahun:
        for h, hl in norm:
            if not col_tahun and any(k in hl for k in ['tahun masuk', 'angkatan', 'thn masuk', 'year entry']):
                col_tahun = h; break
    if not col_tahun:
        for h, hl in norm:
            if not col_tahun and any(k in hl for k in ['tahun', 'year']):
                col_tahun = h; break

    # --- PRODI ---
    for h, hl in norm:
        if not col_prodi and any(k in hl for k in ['program studi', 'prog studi', 'prodi']):
            col_prodi = h; break
    if not col_prodi:
        for h, hl in norm:
            if not col_prodi and any(k in hl for k in ['jurusan', 'program', 'studi', 'major', 'dept', 'department']):
                col_prodi = h; break

    # --- NIM & FAKULTAS (opsional) ---
    for h, hl in norm:
        if not col_nim and any(k in hl for k in ['nim', 'nrp', 'nip', 'student id']):
            col_nim = h; break
    for h, hl in norm:
        if not col_fakultas and any(k in hl for k in ['fakultas', 'faculty', 'fakulti']):
            col_fakultas = h; break

    return {
        'nama': col_nama,
        'tahun': col_tahun,
        'prodi': col_prodi,
        'nim': col_nim,
        'fakultas': col_fakultas,
    }


@app.route("/api/detect_csv", methods=["POST"])
@login_required
def detect_csv():
    file = request.files.get('file')
    if not file:
        return jsonify({"status": "error", "message": "Tidak ada file"})
    try:
        content = file.read().decode('utf-8', errors='replace')
        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []

        cols = _detect_columns(headers)

        # Format untuk response (backward compat dengan frontend)
        col_map = {}
        if cols['nama']:       col_map['nama']          = cols['nama']
        if cols['tahun']:      col_map['tahun_masuk']   = cols['tahun']   # key lama, value kolom yg terdeteksi
        if cols['prodi']:      col_map['program_studi'] = cols['prodi']
        if cols['nim']:        col_map['nim']           = cols['nim']
        if cols['fakultas']:   col_map['fakultas']      = cols['fakultas']

        return jsonify({"status": "detected", "columns": col_map, "all_headers": headers})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/import_csv", methods=["POST"])
@login_required
def import_csv_api():
    file = request.files.get('file')
    if not file:
        return jsonify({"status": "error", "message": "Tidak ada file"})
    try:
        content = file.read().decode('utf-8', errors='replace')
        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []

        cols = _detect_columns(headers)
        col_nama  = cols['nama']
        col_tahun = cols['tahun']
        col_prodi = cols['prodi']

        if not col_nama:
            return jsonify({"status": "error", "message": f"Kolom nama tidak ditemukan. Header CSV: {headers}"})

        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM alumni")

        imported = 0
        errors = 0
        error_details = []

        for row in reader:
            try:
                nama = str(row.get(col_nama, '')).strip()
                if not nama:
                    continue
                tahun = str(row.get(col_tahun, '')).strip() if col_tahun else ''
                prodi = str(row.get(col_prodi, '')).strip() if col_prodi else ''
                cur.execute("INSERT INTO alumni (nama, tahun, prodi) VALUES (?,?,?)", (nama, tahun, prodi))
                imported += 1
            except Exception as e:
                errors += 1
                error_details.append(str(e))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "imported": imported, "errors": errors, "error_details": error_details[:5]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


# ===============================
# HALAMAN TRACKING
# ===============================

@app.route("/tracking")
def tracking():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM alumni")
    alumni = cur.fetchall()
    conn.close()
    return render_template("tracking.html", alumni=alumni)



# ===============================
# MENJALANKAN SERVER
# ===============================

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
