# app.py — Flask + Postgres (PC board giữ nguyên, thêm Android upload)
from flask import Flask, request, jsonify, render_template_string, send_from_directory
import os, sys, json, threading, traceback, re, secrets, time
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, timezone

app = Flask(__name__)
LOCK = threading.Lock()

# ====== CONFIG ======
TOKEN        = os.environ.get("API_TOKEN", "POKEMONVIETNAM")
DATABASE_URL = os.environ.get("DATABASE_URL")  # Neon URL

def log(*a): print(*a); sys.stdout.flush()
def safe_int(x, default=0):
    try: return int(str(x).strip())
    except: return default

def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL")
    return psycopg.connect(DATABASE_URL, autocommit=True)

def init_db():
    with db_conn() as con, con.cursor() as cur:
        # PC board (đÃ có)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS scores(
          uid        TEXT PRIMARY KEY,
          name       TEXT NOT NULL,
          rounds     INTEGER NOT NULL DEFAULT 0,
          kos        INTEGER NOT NULL DEFAULT 0,
          trainers   INTEGER NOT NULL DEFAULT 0,
          extra      INTEGER NOT NULL DEFAULT 0,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        # Android device registry
        cur.execute("""
        CREATE TABLE IF NOT EXISTS devices(
          uid        TEXT PRIMARY KEY,
          secret     TEXT NOT NULL,
          last_seq   INTEGER NOT NULL DEFAULT 0,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        # Android events (idempotent)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS events_android(
          uid        TEXT    NOT NULL,
          seq        INTEGER NOT NULL,
          ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
          action     TEXT    NOT NULL,
          rounds     INTEGER NOT NULL,
          kos        INTEGER NOT NULL,
          trainers   INTEGER NOT NULL,
          extra      INTEGER NOT NULL,
          payload    JSONB   NOT NULL,
          PRIMARY KEY (uid, seq)
        )""")
        # Android aggregate table (bảng BXH Android)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS scores_android(
          uid        TEXT PRIMARY KEY,
          name       TEXT NOT NULL,
          rounds     INTEGER NOT NULL DEFAULT 0,
          kos        INTEGER NOT NULL DEFAULT 0,
          trainers   INTEGER NOT NULL DEFAULT 0,
          extra      INTEGER NOT NULL DEFAULT 0,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
init_db()

# ======= PC API GIỮ NGUYÊN (report/set/delta) =======

@app.route("/api/health")
def health():
    return jsonify(ok=True)

# ------ PC: /api/report (giữ như bạn đã dùng) ------
@app.route("/api/report", methods=["POST"])
def report_pc():
    try:
        data = request.form.to_dict() or (request.get_json(silent=True) or {})
        if not data:                         return jsonify(error="no data"), 400
        if data.get("token") != TOKEN:       return jsonify(error="bad token"), 401

        action   = (data.get("action") or "set").lower()
        name     = (data.get("name")   or "Unknown").strip()[:40]
        uid      = (data.get("uid")    or ("__name__:"+name)).strip()[:64]
        rounds   = safe_int(data.get("rounds"))
        kos      = safe_int(data.get("kos"))
        trainers = safe_int(data.get("trainers"))
        extra    = safe_int(data.get("extra"))

        with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
            if action == "delta":
                cur.execute("""
                  INSERT INTO scores(uid,name,rounds,kos,trainers,extra)
                  VALUES (%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (uid) DO UPDATE
                    SET name=EXCLUDED.name,
                        rounds=scores.rounds+EXCLUDED.rounds,
                        kos=scores.kos+EXCLUDED.kos,
                        trainers=scores.trainers+EXCLUDED.trainers,
                        extra=scores.extra+EXCLUDED.extra,
                        updated_at=now()
                """, (uid, name, rounds, kos, trainers, extra))
            else:
                cur.execute("""
                  INSERT INTO scores(uid,name,rounds,kos,trainers,extra)
                  VALUES (%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (uid) DO UPDATE
                    SET name=EXCLUDED.name,
                        rounds=EXCLUDED.rounds,
                        kos=EXCLUDED.kos,
                        trainers=EXCLUDED.trainers,
                        extra=EXCLUDED.extra,
                        updated_at=now()
                """, (uid, name, rounds, kos, trainers, extra))
            cur.execute("SELECT name,rounds,kos,trainers,extra FROM scores WHERE uid=%s", (uid,))
            row = cur.fetchone()
        return jsonify(ok=True, uid=uid, **row)
    except Exception as e:
        log("[REPORT_PC][ERROR]", e, traceback.format_exc())
        return jsonify(error="internal", detail=str(e)), 500

# ====== UI template tái sử dụng ======
TPL = r"""
<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
:root{--bg:#0e111a;--fg:#e8ebf7;--card:#101627;--muted:#a7aec6;--border:#20273a;--row:#0f1424;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:500 16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Helvetica Neue,Arial,Noto Sans,sans-serif}
.container{max-width:1060px;margin:24px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.25);overflow:hidden}
.header{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--border)}
.left{display:flex;align-items:center;gap:12px;flex:1}
.h1{font-size:20px;font-weight:800}
.table-wrap{overflow:auto}
table{width:100%;border-collapse:separate;border-spacing:0}
th,td{padding:12px 14px;border-bottom:1px solid var(--border);white-space:nowrap}
th{position:sticky;top:0;background:var(--card);z-index:1;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
tbody tr:nth-child(even){background:var(--row)}
.rank{text-align:center;font-weight:800}
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 8px;border-radius:999px;border:1px solid var(--border);background:#0b1222}
.footer{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;color:var(--muted);font-size:13px}
.uploader{padding:12px 16px;border-top:1px solid var(--border);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input[type=file]{color:var(--fg)}
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <div class="header"><div class="left"><div class="h1">{{ heading }}</div></div></div>
    <div class="table-wrap">
      <table id="board">
        <thead><tr><th>#</th><th>Tên</th><th>Rounds</th><th>KOs</th><th>Trainers</th><th>Extra</th></tr></thead>
        <tbody>
        {% for name,row in rows %}
          <tr>
            <td class="rank"><span class="badge">{{ loop.index }}</span></td>
            <td>{{ name }}</td>
            <td>{{ row.rounds }}</td>
            <td>{{ row.kos }}</td>
            <td>{{ row.trainers }}</td>
            <td>{{ row.extra }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% if upload %}
    <form class="uploader" method="post" action="/api/android/upload" enctype="multipart/form-data">
      <b>Upload file Android (.bxh):</b>
      <input type="file" name="file" accept=".bxh,.txt" required>
      <button>Gửi</button>
      <small>(File sinh bởi game trên JoiPlay)</small>
    </form>
    {% endif %}
    <div class="footer"><span>Hiển thị {{ rows|length }} người chơi</span><span>⏱️ {{ now }}</span></div>
  </div>
</div>
</body></html>
"""

# ====== PC board view giữ nguyên ======
@app.route("/")
@app.route("/board")
def board_pc():
    with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
        cur.execute("""
          SELECT uid,name,rounds,kos,trainers,extra
          FROM scores
          ORDER BY trainers DESC, kos DESC, rounds DESC, extra DESC, updated_at DESC
        """)
        recs = cur.fetchall()
    rows = [(r["name"], {"rounds":r["rounds"],"kos":r["kos"],"trainers":r["trainers"],"extra":r["extra"]}) for r in recs]
    return render_template_string(TPL,
        title="BXH Pokémon Việt Nam (PC)",
        heading="BXH Pokémon Việt Nam — PC",
        rows=rows, now=datetime.now().strftime("%H:%M:%S"), upload=False)

# ====== Android: helpers ======
def parse_kv_or_json(text:str):
    text = text.strip()
    # JSON?
    if text.startswith("{"):
        obj = json.loads(text)
        return {k:str(v) if isinstance(v,(int,float)) else v for k,v in obj.items()}
    # KV lines: key=value
    out = {}
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith("#"): continue
        if "=" not in line: continue
        k,v = line.split("=",1)
        out[k.strip()] = v.strip()
    return out

def canonical_string(rec:dict):
    # Chuẩn hóa theo cùng thứ tự bên client
    name = (rec.get("name") or "").replace("|"," ").replace("\n"," ").replace("\r"," ")
    return "%s|%s|%s|%s|%s|%s|%s|%s|%s" % (
        rec.get("uid",""),
        str(int(rec.get("seq",0))),
        str(int(rec.get("ts",0))),
        (rec.get("action","delta") or "delta"),
        str(int(rec.get("rounds",0))),
        str(int(rec.get("kos",0))),
        str(int(rec.get("trainers",0))),
        str(int(rec.get("extra",0))),
        name
    )

import hmac, hashlib
def hmac_sha1_hex(key:str, data:str):
    return hmac.new(key.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).hexdigest()

def sanity_ok(rec, last_seq):
    # Giới hạn đơn giản để chặn delta vô lý (có thể điều chỉnh)
    r = safe_int(rec.get("rounds",0))
    k = safe_int(rec.get("kos",0))
    t = safe_int(rec.get("trainers",0))
    e = safe_int(rec.get("extra",0))
    if r < 0 or k < 0 or e < 0: return False, "negative"
    if t not in (0,1): return False, "trainers must be 0 or 1"
    if k > 6*max(1,r): return False, "kos too large"
    if safe_int(rec.get("seq",0)) - last_seq > 200: return False, "seq jump too large"
    ts = safe_int(rec.get("ts",0))
    if abs(int(time.time()) - ts) > 60*60*24*3: return False, "ts too far"
    return True, ""

# ====== Android upload ======
@app.route("/api/android/upload", methods=["POST"])
def upload_android():
    try:
        # Nhận file
        if "file" in request.files:
            raw = request.files["file"].read().decode("utf-8","ignore")
        else:
            raw = (request.form.get("data") or request.data.decode("utf-8","ignore"))
        if not raw: return jsonify(error="no file"), 400

        rec = parse_kv_or_json(raw)
        required = ["uid","name","seq","ts","action","rounds","kos","trainers","extra","sig"]
        if any(x not in rec for x in required):
            return jsonify(error="bad file", missing=[x for x in required if x not in rec]), 400

        uid = (rec["uid"] or "").strip()[:64]
        name = (rec["name"] or "Unknown").strip()[:40]
        seq = safe_int(rec["seq"])
        sig = (rec["sig"] or "").lower()

        with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
            # Lấy secret hoặc đăng ký lần đầu
            cur.execute("SELECT secret,last_seq FROM devices WHERE uid=%s", (uid,))
            row = cur.fetchone()
            if not row:
                sec = (rec.get("sec") or "").strip()
                if not re.fullmatch(r"[a-zA-Z0-9\-]{8,64}", sec or ""):
                    return jsonify(error="unregistered", detail="missing/invalid secret"), 401
                cur.execute("INSERT INTO devices(uid,secret,last_seq) VALUES (%s,%s,%s)", (uid, sec, 0))
                secret = sec
                last_seq = 0
            else:
                secret = row["secret"]
                last_seq = int(row["last_seq"])

            # Verify chữ ký
            s = canonical_string(rec)
            expect = hmac_sha1_hex(secret, s)
            if expect != sig:
                return jsonify(error="bad signature"), 401

            # Idempotent: ghi sự kiện trước
            cur.execute("""
              INSERT INTO events_android(uid,seq,ts,action,rounds,kos,trainers,extra,payload)
              VALUES (%s,%s,to_timestamp(%s),%s,%s,%s,%s,%s,%s)
              ON CONFLICT (uid,seq) DO NOTHING
            """, (uid, seq, safe_int(rec["ts"]), rec["action"], safe_int(rec["rounds"]),
                  safe_int(rec["kos"]), safe_int(rec["trainers"]), safe_int(rec["extra"]),
                  json.dumps(rec, ensure_ascii=False)))
            if cur.rowcount == 0:
                # trùng file (seq cũ)
                return jsonify(ok=True, uid=uid, duplicate=True)

            # Sanity
            ok, why = sanity_ok(rec, last_seq)
            if not ok:
                return jsonify(error="sanity", detail=why), 400

            # Cập nhật bảng điểm Android
            if (rec.get("action") or "delta").lower() == "delta":
                cur.execute("""
                  INSERT INTO scores_android(uid,name,rounds,kos,trainers,extra)
                  VALUES (%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (uid) DO UPDATE
                    SET name=EXCLUDED.name,
                        rounds=scores_android.rounds+EXCLUDED.rounds,
                        kos=scores_android.kos+EXCLUDED.kos,
                        trainers=scores_android.trainers+EXCLUDED.trainers,
                        extra=scores_android.extra+EXCLUDED.extra,
                        updated_at=now()
                """,(uid, name, safe_int(rec["rounds"]), safe_int(rec["kos"]),
                     safe_int(rec["trainers"]), safe_int(rec["extra"])))
            else:
                cur.execute("""
                  INSERT INTO scores_android(uid,name,rounds,kos,trainers,extra)
                  VALUES (%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (uid) DO UPDATE
                    SET name=EXCLUDED.name,
                        rounds=EXCLUDED.rounds,
                        kos=EXCLUDED.kos,
                        trainers=EXCLUDED.trainers,
                        extra=EXCLUDED.extra,
                        updated_at=now()
                """,(uid, name, safe_int(rec["rounds"]), safe_int(rec["kos"]),
                     safe_int(rec["trainers"]), safe_int(rec["extra"])))
            # update last_seq
            cur.execute("UPDATE devices SET last_seq=%s, updated_at=now() WHERE uid=%s", (seq, uid))

        return jsonify(ok=True, uid=uid)
    except Exception as e:
        log("[ANDROID][ERROR]", e, traceback.format_exc())
        return jsonify(error="internal", detail=str(e)), 500

# ====== Android board ======
@app.route("/android")
def board_android():
    with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
        cur.execute("""
          SELECT uid,name,rounds,kos,trainers,extra
          FROM scores_android
          ORDER BY trainers DESC, kos DESC, rounds DESC, extra DESC, updated_at DESC
        """)
        recs = cur.fetchall()
    rows = [(r["name"], {"rounds":r["rounds"],"kos":r["kos"],"trainers":r["trainers"],"extra":r["extra"]}) for r in recs]
    return render_template_string(TPL,
        title="BXH Pokémon Việt Nam (Android)",
        heading="BXH Pokémon Việt Nam — Android (JoiPlay)",
        rows=rows, now=datetime.now().strftime("%H:%M:%S"), upload=True)

@app.route("/api/android/raw")
def raw_android():
    with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM scores_android ORDER BY trainers DESC, kos DESC")
        return jsonify(cur.fetchall())

@app.route("/api/android/clear", methods=["POST"])
def clear_android():
    if (request.form.get("token") or "") != TOKEN:
        return jsonify(error="bad token"), 401
    with db_conn() as con, con.cursor() as cur:
        cur.execute("TRUNCATE TABLE events_android")
        cur.execute("TRUNCATE TABLE scores_android")
        cur.execute("UPDATE devices SET last_seq=0")
    return jsonify(ok=True)

# tĩnh nếu có
@app.route("/static/<path:fname>")
def static_files(fname):
    return send_from_directory("static", fname)

# ==== (tuỳ chọn) Mint UID API cho PC, không dùng cho Android upload ====
@app.route("/api/mint_uid", methods=["POST"])
def mint_uid():
    if (request.form.get("token") or "") != TOKEN:
        return jsonify(error="bad token"), 401
    while True:
        uid = "RG1-" + secrets.token_hex(6).upper()
        with db_conn() as con, con.cursor() as cur:
            cur.execute("SELECT 1 FROM scores WHERE uid=%s LIMIT 1", (uid,))
            cur2 = cur.fetchone()
            cur.execute("SELECT 1 FROM scores_android WHERE uid=%s LIMIT 1", (uid,))
            cur3 = cur.fetchone()
            if not cur2 and not cur3:
                break
    return jsonify(ok=True, uid=uid)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","10000")))
