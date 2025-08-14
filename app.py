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
TPL = r"""
<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BXH Pokémon Việt Nam</title>
<style>
:root{
  --bg:#f7f8fc; --fg:#0b1021; --card:#ffffff; --accent:#ffcc00; --accent2:#3b4cca;
  --muted:#8a8fa3; --border:#e6e8f2; --row:#fafbff;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#0e111a; --fg:#e6e9f5; --card:#121623; --accent2:#7289da;
         --muted:#aab1c6; --border:#242a3a; --row:#151a28; }
}
*{box-sizing:border-box}
body{
  margin:0;
  background:
    radial-gradient(1200px 800px at 10% -10%, rgba(255,204,0,.08), transparent),
    radial-gradient(900px 700px at 110% 10%, rgba(59,76,202,.10), transparent),
    var(--bg);
  color:var(--fg);
  font:500 16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Helvetica Neue,Arial,Noto Sans,sans-serif;
}
.container{max-width:980px;margin:24px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;
      box-shadow:0 8px 30px rgba(0,0,0,.06);overflow:hidden}
.header{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--border);
        background:linear-gradient(to right, rgba(255,204,0,.12), rgba(59,76,202,.12))}
.logo{width:28px;height:28px;display:inline-block}
.h1{font-size:20px;font-weight:800;letter-spacing:.3px}
.controls{display:flex;gap:10px;margin-left:auto}
input[type=search]{appearance:none;border:1px solid var(--border);background:transparent;color:var(--fg);
  padding:8px 10px;border-radius:10px;min-width:160px}
.refresh{border:1px solid var(--border);background:transparent;padding:8px 10px;border-radius:10px;cursor:pointer}
.table-wrap{overflow:auto}
table{width:100%;border-collapse:separate;border-spacing:0}
th,td{padding:12px 14px;border-bottom:1px solid var(--border);white-space:nowrap}
th{position:sticky;top:0;background:var(--card);z-index:1;font-size:13px;color:var(--muted);
   text-transform:uppercase;letter-spacing:.08em}
tbody tr:nth-child(even){background:var(--row)}
.rank{font-weight:800;text-align:center}
.name{font-weight:700}
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 8px;border-radius:999px;border:1px solid var(--border);
       font-variant-numeric:tabular-nums}
.medal-1{background:linear-gradient(180deg,#fff3bf,#ffe066);border-color:#ffd43b}
.medal-2{background:linear-gradient(180deg,#f1f3f5,#dee2e6);border-color:#adb5bd}
.medal-3{background:linear-gradient(180deg,#ffe8cc,#ffc078);border-color:#ffa94d}
.footer{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;color:var(--muted);font-size:13px}
@media (max-width:640px){
  .h1{font-size:18px}
  th,td{padding:10px 12px}
  .controls{gap:6px}
  input[type=search]{min-width:120px}
}
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <div class="header">
      <!-- Pokéball mini bằng SVG -->
      <svg class="logo" viewBox="0 0 32 32" fill="none" aria-hidden="true">
        <circle cx="16" cy="16" r="14" stroke="var(--accent2)" stroke-width="4"/>
        <path d="M2 16h28" stroke="var(--fg)" stroke-width="4"/>
        <circle cx="16" cy="16" r="5" fill="var(--accent)" stroke="var(--fg)" stroke-width="2"/>
      </svg>
      <div class="h1">BXH Pokémon Việt Nam</div>
      <div class="controls">
        <input id="q" type="search" placeholder="Tìm người chơi…">
        <button class="refresh" onclick="location.reload()">↻ Tải lại</button>
      </div>
    </div>

    <div class="table-wrap">
      <table id="board">
        <thead>
          <tr>
            <th>#</th>
            <th>Tên</th>
            <th>Rounds</th>
            <th>KOs</th>
            <th>Trainers</th>
            <th>Extra</th>
          </tr>
        </thead>
        <tbody>
          {% for name, row in rows %}
          <tr>
            <td class="rank">
              {% if loop.index==1 %}
                <span class="badge medal-1">🥇 {{loop.index}}</span>
              {% elif loop.index==2 %}
                <span class="badge medal-2">🥈 {{loop.index}}</span>
              {% elif loop.index==3 %}
                <span class="badge medal-3">🥉 {{loop.index}}</span>
              {% else %}
                <span class="badge">{{loop.index}}</span>
              {% endif %}
            </td>
            <td class="name">{{ name }}</td>
            <td>{{ row.rounds }}</td>
            <td>{{ row.kos }}</td>
            <td>{{ row.trainers }}</td>
            <td>{{ row.extra }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <div class="footer">
      <span>Hiển thị {{ rows|length }} người chơi</span>
      <span>⏱️ Cập nhật: {{ updated_at }}</span>
    </div>
  </div>
</div>

<script>
const q = document.getElementById('q');
q?.addEventListener('input', () => {
  const term = q.value.toLowerCase();
  document.querySelectorAll('#board tbody tr').forEach(tr => {
    const name = tr.children[1].textContent.toLowerCase();
    tr.style.display = name.includes(term) ? '' : 'none';
  });
});
</script>
</body>
</html>
"""

from datetime import datetime, timezone

@app.route("/")
@app.route("/board")
def board():
    db = load_db()
    rows = sorted(
        db.items(),
        key=lambda kv: (kv[1].get("rounds", 0), kv[1].get("kos", 0)),
        reverse=True
    )
    now = datetime.now().strftime("%H:%M:%S")
    return render_template_string(TPL, rows=rows, updated_at=now)

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

