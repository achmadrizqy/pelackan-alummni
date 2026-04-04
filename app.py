from flask import Flask, render_template, request, redirect, session, flash
from functools import wraps
import random
import sqlite3

app = Flask(__name__)
app.secret_key = 'super_secret_key_alumni_tracking'  # Digunakan untuk amankan session

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
        pekerjaan TEXT,
        instansi TEXT,
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

    # Tambahkan user default jika belum ada
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
            flash('Silakan login terlebih dahulu untuk mengakses halaman ini.', 'warning')
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

# ===============================
# DATA SEMENTARA (SIMULASI DATABASE)
# ===============================

data_alumni = [
    {"nama": "rizqy", "tahun": "2021", "prodi": "informatika"},
    {"nama": "supaidi", "tahun": "2019", "prodi": "informatika"}
]

hasil_pelacakan = []


# ===============================
# DASHBOARD
# ===============================

@app.route("/")
def index():
    return render_template("index.html")


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
# HALAMAN KELOLA ALUMNI
# ===============================

@app.route("/alumni", methods=["GET", "POST"])
@login_required
def alumni():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        nama = request.form["nama"]
        tahun = request.form["tahun"]
        prodi = request.form["prodi"]

        cur.execute(
            "INSERT INTO alumni (nama,tahun,prodi) VALUES (?,?,?)",
            (nama, tahun, prodi)
        )
        conn.commit()
        return redirect("/alumni")

    cur.execute("SELECT * FROM alumni")
    data = cur.fetchall()
    conn.close()
    return render_template("alumni.html", alumni=data)


# ===============================
# HALAMAN PELACAKAN
# ===============================

@app.route("/tracking")
@login_required
def tracking():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM alumni")
    alumni = cur.fetchall()
    conn.close()
    return render_template("tracking.html", alumni=alumni)


# ===============================
# MENJALANKAN PROSES PELACAKAN
# ===============================

@app.route("/jalankan_pelacakan")
@login_required
def jalankan_pelacakan():

    conn = get_db()
    cur = conn.cursor()

    pekerjaan_list = [
        "Software Engineer",
        "Data Analyst",
        "Researcher",
        "System Administrator"
    ]

    instansi_list = [
        "Startup Teknologi",
        "Perusahaan IT",
        "Universitas",
        "Perusahaan Swasta"
    ]

    sumber_list = [
        "Google",
        "LinkedIn",
        "Instagram",
        "Scholar",
        "ORCID",
        "ResearchGate",
        "GitHub",
        "Kaggle",
        "Directory Perusahaan",
        "Press Release",
        "Publikasi Akademik",
        "Web Umum"
    ]

    cur.execute("SELECT * FROM alumni")
    alumni = cur.fetchall()

    for a in alumni:
        pekerjaan = random.choice(pekerjaan_list)
        instansi = random.choice(instansi_list)
        sumber = random.choice(sumber_list)
        skor = round(random.uniform(0,1),2)
        if skor > 0.8:
            status = "Teridentifikasi"
        elif skor > 0.5:
            status = "Perlu Verifikasi"
        else:
            status = "Belum Ditemukan"
        cur.execute(
        """
        INSERT INTO hasil_pelacakan 
        (nama, pekerjaan, instansi, confidence, status, sumber)
        VALUES (?,?,?,?,?,?)
        """,
        (a["nama"], pekerjaan, instansi, skor, status, sumber)
        )

    conn.commit()
    conn.close()

    return redirect("/hasil")


# ===============================
# HALAMAN HASIL PELACAKAN
# ===============================

@app.route("/hasil")
@login_required
def hasil():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM hasil_pelacakan")
    hasil = cur.fetchall()
    conn.close()
    return render_template("hasil.html", hasil=hasil)


# ===============================
# MENJALANKAN SERVER
# ===============================

if __name__ == "__main__":
    init_db()
    app.run(debug=True)