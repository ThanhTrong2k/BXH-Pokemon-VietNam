# app.py  — giữ nguyên API, chuyển lưu trữ sang Neon (Postgres)
from flask import Flask, request, jsonify, render_template_string, send_from_directory
import json, os, threading, traceback, sys

# >>> NEW <<<
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
LOCK = threading.Lock()

# ====== CONFIG ======
TOKEN         = os.environ.get("API_TOKEN", "POKEMONVIETNAM")
DATABASE_URL  = os.environ.get("DATABASE_URL")  # lấy từ Neon

def safe_int(x, default=0):
    try: return int(str(x).strip())
    except: return default

def log(msg):
    print(msg); sys.stdout.flush()

# ====== DB HELPERS (Postgres) ======
def db_conn():
    # autocommit để UPDATE/UPSERT chạy thẳng
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL (Neon)")
    return psycopg.connect(DATABASE_URL, autocommit=True)

def init_db():
    with db_conn() as con, con.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS scores (
          name       TEXT PRIMARY KEY,
          rounds     INTEGER NOT NULL DEFAULT 0,
          kos        INTEGER NOT NULL DEFAULT 0,
          trainers   INTEGER NOT NULL DEFAULT 0,
          extra      INTEGER NOT NULL DEFAULT 0,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
init_db()

# (giữ tên hàm cũ nhưng đọc/ghi từ Postgres để các view hiện tại không phải đổi)
def load_db():
    """Trả về dict {name: {rounds, kos, trainers, extra}} từ Postgres."""
    data = {}
    with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT name, rounds, kos, trainers, extra FROM scores")
        for r in cur.fetchall():
            data[r["name"]] = {
                "rounds":   int(r["rounds"]),
                "kos":      int(r["kos"]),
                "trainers": int(r["trainers"]),
                "extra":    int(r["extra"]),
            }
    return data

def save_db(db):
    """
    Tương thích clear(): nếu db == {} -> TRUNCATE.
    Không dùng ở report().
    """
    with db_conn() as con, con.cursor() as cur:
        if not db:
            cur.execute("TRUNCATE TABLE scores")
            return
        # nếu ai đó gọi save_db với dữ liệu đầy đủ
        for name, row in db.items():
            cur.execute("""
              INSERT INTO scores(name, rounds, kos, trainers, extra)
              VALUES (%s,%s,%s,%s,%s)
              ON CONFLICT (name) DO UPDATE
                 SET rounds=EXCLUDED.rounds,
                     kos=EXCLUDED.kos,
                     trainers=EXCLUDED.trainers,
                     extra=EXCLUDED.extra,
                     updated_at=now()
            """, (name, row.get("rounds",0), row.get("kos",0),
                  row.get("trainers",0), row.get("extra",0)))

# ===== API =====
@app.route("/api/health")
def health():
    return jsonify(ok=True)

@app.route("/api/report", methods=["POST"])
def report():
    try:
        data = request.form.to_dict() or (request.get_json(silent=True) or {})
        log(f"[REPORT] payload={data}")

        if not data:
            return jsonify(error="no data"), 400
        if data.get("token") != TOKEN:
            return jsonify(error="bad token"), 401

        action   = (data.get("action") or "set").lower()
        name     = (data.get("name")   or "Unknown").strip()[:40]
        rounds   = safe_int(data.get("rounds"))
        kos      = safe_int(data.get("kos"))
        trainers = safe_int(data.get("trainers"))
        extra    = safe_int(data.get("extra"))

        with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
            if action == "delta":
                # Cộng dồn (UPSERT)
                cur.execute("""
                  INSERT INTO scores(name, rounds, kos, trainers, extra)
                  VALUES (%s,%s,%s,%s,%s)
                  ON CONFLICT (name) DO UPDATE
                    SET rounds   = scores.rounds   + EXCLUDED.rounds,
                        kos      = scores.kos      + EXCLUDED.kos,
                        trainers = scores.trainers + EXCLUDED.trainers,
                        extra    = scores.extra    + EXCLUDED.extra,
                        updated_at = now()
                """, (name, rounds, kos, trainers, extra))
            else:
                # Ghi tuyệt đối (UPSERT)
                cur.execute("""
                  INSERT INTO scores(name, rounds, kos, trainers, extra)
                  VALUES (%s,%s,%s,%s,%s)
                  ON CONFLICT (name) DO UPDATE
                    SET rounds   = EXCLUDED.rounds,
                        kos      = EXCLUDED.kos,
                        trainers = EXCLUDED.trainers,
                        extra    = EXCLUDED.extra,
                        updated_at = now()
                """, (name, rounds, kos, trainers, extra))

            cur.execute("SELECT rounds, kos, trainers, extra FROM scores WHERE name=%s", (name,))
            row = cur.fetchone()

        return jsonify(ok=True, name=name, **row)
    except Exception as e:
        log(f"[REPORT][ERROR] {e}\n{traceback.format_exc()}")
        return jsonify(error="internal", detail=str(e)), 500

# ===== View BXH =====
TPL = """
<!doctype html><meta charset="utf-8">
<title>BXH</title>
<h2>BXH Pokémon Việt Nam</h2>
<table border=1 cellpadding=6 cellspacing=0>
<tr><th>#</th><th>Tên</th><th>Rounds</th><th>KOs</th><th>Trainers</th><th>Extra</th></tr>
{% for name, row in rows %}
<tr>
  <td>{{ loop.index }}</td>
  <td>{{ name }}</td>
  <td>{{ row.rounds }}</td>
  <td>{{ row.kos }}</td>
  <td>{{ row.trainers }}</td>
  <td>{{ row.extra }}</td>
</tr>
{% endfor %}
</table>
"""

@app.route("/")
@app.route("/board")
def board():
    db = load_db()
    rows = sorted(
        db.items(),
        key=lambda kv: (kv[1].get("rounds", 0), kv[1].get("kos", 0)),
        reverse=True
    )
    return render_template_string(TPL, rows=rows)

@app.route("/static/<path:fname>")
def static_files(fname):
    return send_from_directory("static", fname)

@app.route("/api/raw")
def raw():
    return jsonify(load_db())

@app.route("/api/clear", methods=["POST"])
def clear():
    if (request.form.get("token") or "") != TOKEN:
        return jsonify(error="bad token"), 401
    save_db({})   # TRUNCATE
    return jsonify(ok=True)

FORM = """
<!doctype html><meta charset="utf-8"><title>Send</title>
<h3>Gửi BXH (test)</h3>
<form method="post" action="/api/report">
  <input type="hidden" name="token" value='""" + TOKEN + """'>
  Action: <select name="action"><option>set</option><option>delta</option></select><br>
  Name: <input name="name" value="TEST_PC"><br>
  Rounds: <input name="rounds" value="5"><br>
  KOs: <input name="kos" value="2"><br>
  Trainers: <input name="trainers" value="1"><br>
  Extra: <input name="extra" value="0"><br>
  <button type="submit">Send</button>
</form>
"""
@app.route("/send")
def send_form():
    return FORM

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
