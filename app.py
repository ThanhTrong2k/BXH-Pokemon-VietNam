from flask import Flask, request, jsonify, render_template_string, redirect, url_for, send_from_directory
import os, json, hmac, hashlib, base64, time, threading, traceback, sys
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
LOCK = threading.Lock()

# ========= CONFIG =========
TOKEN        = os.environ.get("API_TOKEN", "POKEMONVIETNAM")    # PC API token (giữ nguyên)
DATABASE_URL = os.environ.get("DATABASE_URL")                   # Neon
UPLOAD_KEY   = (os.environ.get("UPLOAD_KEY") or "CHANGE_ME")    # bí mật HMAC cho Android file

def log(msg): print(msg); sys.stdout.flush()

# ========= DB =========
def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL")
    return psycopg.connect(DATABASE_URL, autocommit=True)

def init_db():
    with db_conn() as con, con.cursor() as cur:
        # PC: giữ nguyên (nếu bạn đã có table này thì lệnh CREATE IF NOT EXISTS sẽ bỏ qua)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS scores (
          uid        TEXT PRIMARY KEY,
          name       TEXT NOT NULL,
          rounds     INTEGER NOT NULL DEFAULT 0,
          kos        INTEGER NOT NULL DEFAULT 0,
          trainers   INTEGER NOT NULL DEFAULT 0,
          extra      INTEGER NOT NULL DEFAULT 0,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        # ANDROID: bảng riêng
        cur.execute("""
        CREATE TABLE IF NOT EXISTS android_scores (
          uid        TEXT PRIMARY KEY,
          name       TEXT NOT NULL,
          rounds     INTEGER NOT NULL DEFAULT 0,
          kos        INTEGER NOT NULL DEFAULT 0,
          trainers   INTEGER NOT NULL DEFAULT 0,
          extra      INTEGER NOT NULL DEFAULT 0,
          last_ts    BIGINT NOT NULL DEFAULT 0,      -- chống replay
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
init_db()

# ========= PC API (giữ y nguyên hành vi cũ) =========
@app.route("/api/report", methods=["POST"])
def report_pc():
    try:
        data = request.form.to_dict() or (request.get_json(silent=True) or {})
        if not data:                return jsonify(error="no data"), 400
        if data.get("token") != TOKEN: return jsonify(error="bad token"), 401

        action   = (data.get("action") or "set").lower()
        name     = (data.get("name")   or "Unknown").strip()[:40]
        uid      = (data.get("uid")    or ("__name__:"+name))
        rounds   = int(str(data.get("rounds") or 0))
        kos      = int(str(data.get("kos") or 0))
        trainers = int(str(data.get("trainers") or 0))
        extra    = int(str(data.get("extra") or 0))

        with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
            if action == "delta":
                cur.execute("""
                  INSERT INTO scores(uid, name, rounds, kos, trainers, extra)
                  VALUES (%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (uid) DO UPDATE
                    SET name = EXCLUDED.name,
                        rounds = scores.rounds + EXCLUDED.rounds,
                        kos = scores.kos + EXCLUDED.kos,
                        trainers = scores.trainers + EXCLUDED.trainers,
                        extra = scores.extra + EXCLUDED.extra,
                        updated_at = now()
                """, (uid, name, rounds, kos, trainers, extra))
            else:
                cur.execute("""
                  INSERT INTO scores(uid, name, rounds, kos, trainers, extra)
                  VALUES (%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (uid) DO UPDATE
                    SET name = EXCLUDED.name,
                        rounds = EXCLUDED.rounds,
                        kos = EXCLUDED.kos,
                        trainers = EXCLUDED.trainers,
                        extra = EXCLUDED.extra,
                        updated_at = now()
                """, (uid, name, rounds, kos, trainers, extra))
            cur.execute("SELECT name, rounds, kos, trainers, extra FROM scores WHERE uid=%s", (uid,))
            row = cur.fetchone()
        return jsonify(ok=True, uid=uid, **row)
    except Exception as e:
        log(f"[PC][ERROR] {e}\n{traceback.format_exc()}")
        return jsonify(error="internal", detail=str(e)), 500

# ========= ANDROID UPLOAD =========
def _hmac_sig(uid, name, action, rounds, kos, trainers, extra, ts):
    msg = f"{uid}|{name}|{action}|{rounds}|{kos}|{trainers}|{extra}|{ts}"
    return hmac.new(UPLOAD_KEY.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()

def _parse_android_payload(raw_bytes):
    """
    Chấp nhận:
      - JSON thuần (bytes bắt đầu bằng '{')
      - base64(JSON)
    Trả về dict hoặc raise ValueError.
    """
    data = None
    s = raw_bytes.lstrip()
    if s.startswith(b"{"):
        data = json.loads(raw_bytes.decode("utf-8", "replace"))
    else:
        try:
            dec = base64.b64decode(raw_bytes, validate=True)
            data = json.loads(dec.decode("utf-8", "replace"))
        except Exception:
            raise ValueError("invalid file format")
    return data

@app.route("/api/upload_android", methods=["POST"])
def upload_android():
    try:
        if "file" not in request.files:
            return jsonify(error="no file"), 400
        f = request.files["file"]
        raw = f.read()
        data = _parse_android_payload(raw)

        # yêu cầu các trường
        uid = str(data.get("uid") or "").strip()
        name = (data.get("name") or "Unknown").strip()[:40]
        action = (data.get("action") or "delta").lower()
        rounds = int(str(data.get("rounds") or 0))
        kos = int(str(data.get("kos") or 0))
        trainers = int(str(data.get("trainers") or 0))
        extra = int(str(data.get("extra") or 0))
        ts = int(str(data.get("ts") or 0))
        sig = str(data.get("sig") or "")

        if not uid or not ts or not sig:
            return jsonify(error="missing fields"), 400

        # verify HMAC
        good = _hmac_sig(uid, name, action, rounds, kos, trainers, extra, ts)
        if not hmac.compare_digest(good, sig):
            return jsonify(error="bad signature"), 401

        with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
            # chống replay theo timestamp
            cur.execute("SELECT last_ts FROM android_scores WHERE uid=%s", (uid,))
            row = cur.fetchone()
            if row and ts <= int(row["last_ts"]):
                return jsonify(error="stale", detail="older or equal timestamp"), 409

            if action == "delta":
                cur.execute("""
                  INSERT INTO android_scores(uid, name, rounds, kos, trainers, extra, last_ts)
                  VALUES (%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (uid) DO UPDATE
                    SET name = EXCLUDED.name,
                        rounds = android_scores.rounds + EXCLUDED.rounds,
                        kos = android_scores.kos + EXCLUDED.kos,
                        trainers = android_scores.trainers + EXCLUDED.trainers,
                        extra = android_scores.extra + EXCLUDED.extra,
                        last_ts = GREATEST(android_scores.last_ts, EXCLUDED.last_ts),
                        updated_at = now()
                """, (uid, name, rounds, kos, trainers, extra, ts))
            else:
                cur.execute("""
                  INSERT INTO android_scores(uid, name, rounds, kos, trainers, extra, last_ts)
                  VALUES (%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (uid) DO UPDATE
                    SET name = EXCLUDED.name,
                        rounds = EXCLUDED.rounds,
                        kos = EXCLUDED.kos,
                        trainers = EXCLUDED.trainers,
                        extra = EXCLUDED.extra,
                        last_ts = GREATEST(android_scores.last_ts, EXCLUDED.last_ts),
                        updated_at = now()
                """, (uid, name, rounds, kos, trainers, extra, ts))

        return jsonify(ok=True, uid=uid, name=name, rounds=rounds, kos=kos, trainers=trainers, extra=extra)
    except Exception as e:
        log(f"[ANDROID][ERROR] {e}\n{traceback.format_exc()}")
        return jsonify(error="internal", detail=str(e)), 500

# ========= Views =========

TPL_BASE = r"""
<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<title>{{ title }}</title>
<style>
:root{--bg:#0e111a;--fg:#e8ebf7;--card:#101627;--muted:#a7aec6;--border:#20273a;--row:#0f1424;--accent:#ffcc00;--accent2:#3b4cca}
*{box-sizing:border-box}
html,body{width:100%;min-height:100%;margin:0;overflow-x:hidden}
body{
  background:
    radial-gradient(1200px 800px at 10% -10%, rgba(255,204,0,.06), transparent),
    radial-gradient(900px 700px at 110% 10%, rgba(59,76,202,.10), transparent),
    var(--bg);
  color:var(--fg);
  font:500 16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Helvetica Neue,Arial,Noto Sans,sans-serif;
}
.container{max-width:1060px;margin:24px auto;padding:0 16px;padding-left:max(16px, env(safe-area-inset-left));padding-right:max(16px, env(safe-area-inset-right))}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.25);overflow:hidden}
.header{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--border);
  background:linear-gradient(to right, rgba(255,204,0,.10), rgba(59,76,202,.10))}
.left{display:flex;align-items:center;gap:12px;flex:1}
.h1{font-size:20px;font-weight:800;letter-spacing:.3px}
.controls{display:flex;gap:8px;align-items:center}
input[type=search], select, button{appearance:none;border:1px solid var(--border);background:#0b1222;color:var(--fg);padding:8px 10px;border-radius:10px}
button{cursor:pointer}
.table-wrap{overflow:auto}
table{width:100%;border-collapse:separate;border-spacing:0}
th,td{padding:12px 14px;border-bottom:1px solid var(--border);white-space:nowrap}
th{position:sticky;top:0;background:var(--card);z-index:1;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
tbody tr:nth-child(even){background:var(--row)}
.rank{text-align:center;font-weight:800}
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 8px;border-radius:999px;border:1px solid var(--border);font-variant-numeric:tabular-nums;background:#0b1222}
.footer{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;color:var(--muted);font-size:13px}
@media (max-width:720px){ th,td{padding:10px 12px} .controls{flex-wrap:wrap;justify-content:flex-end}}
/* small upload pill */
.upload-pill{display:flex;gap:8px;align-items:center}
</style>
<div class="container">
  <div class="card">
    <div class="header">
      <div class="left">
        <div class="h1">{{ title }}</div>
      </div>
      <div class="controls">
        {% if show_upload %}
        <form class="upload-pill" method="post" action="/api/upload_android" enctype="multipart/form-data">
          <input type="file" name="file" accept=".bxh,.json,.txt" required>
          <button type="submit">⬆︎ Upload file</button>
        </form>
        {% endif %}
        <input id="q" type="search" placeholder="Tìm người chơi…">
        <select id="sortBy">
          <option value="default">Mặc định (T↓ K↓ R↓ E↓)</option>
          <option value="kos">KOs cao nhất</option>
          <option value="rounds">Rounds cao nhất</option>
          <option value="extra">Extra cao nhất</option>
        </select>
        <button onclick="location.reload()">↻ Tải lại</button>
      </div>
    </div>
    <div class="table-wrap">
      <table id="board">
        <thead>
          <tr><th>#</th><th>Tên</th><th>Rounds</th><th>KOs</th><th>Trainers</th><th>Extra</th></tr>
        </thead>
        <tbody>
        {% for name, row in rows %}
          <tr data-name="{{ name|lower }}" data-rounds="{{ row.rounds }}" data-kos="{{ row.kos }}" data-trainers="{{ row.trainers }}" data-extra="{{ row.extra }}">
            <td class="rank"><span class="badge">{{ loop.index }}</span></td>
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
    <div class="footer"><span>Hiển thị {{ rows|length }} người chơi</span><span id="updatedAt">⏱️</span></div>
  </div>
</div>
<script>
// Filter
const q=document.getElementById('q');
q?.addEventListener('input',()=>{const t=q.value.toLowerCase();document.querySelectorAll('#board tbody tr').forEach(tr=>{tr.style.display=tr.dataset.name.includes(t)?'':'none';});});
// Sort
const sortBy=document.getElementById('sortBy');
function sortTable(mode){
  const tbody=document.querySelector('#board tbody');
  const rows=[...tbody.querySelectorAll('tr')];
  const keyDefault = r=>[+(r.dataset.trainers||0),+(r.dataset.kos||0),+(r.dataset.rounds||0),+(r.dataset.extra||0)];
  const keyKos     = r=>[+(r.dataset.kos||0),+(r.dataset.trainers||0),+(r.dataset.rounds||0),+(r.dataset.extra||0)];
  const keyRounds  = r=>[+(r.dataset.rounds||0),+(r.dataset.trainers||0),+(r.dataset.kos||0),+(r.dataset.extra||0)];
  const keyExtra   = r=>[+(r.dataset.extra||0),+(r.dataset.trainers||0),+(r.dataset.kos||0),+(r.dataset.rounds||0)];
  const getKey = r => (mode==='kos'?keyKos(r):mode==='rounds'?keyRounds(r):mode==='extra'?keyExtra(r):keyDefault(r));
  rows.sort((a,b)=>{const ka=getKey(a),kb=getKey(b);for(let i=0;i<ka.length;i++){if(kb[i]!==ka[i]) return kb[i]-ka[i];}return a.dataset.name.localeCompare(b.dataset.name);});
  rows.forEach((r,i)=>{r.querySelector('.badge').textContent=(i+1);tbody.appendChild(r);});
}
sortBy?.addEventListener('change',()=>sortTable(sortBy.value));
// Clock
function pad(n){return n<10?'0'+n:n} ; function tick(){const d=new Date();document.getElementById('updatedAt').textContent=`⏱️ Cập nhật: ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;}
tick(); setInterval(tick,1000);
</script>
"""

def _rows_from(cur, table):
    cur.execute(f"SELECT name, rounds, kos, trainers, extra FROM {table}")
    items = cur.fetchall()
    rows = sorted(
        [(r["name"], {"rounds":r["rounds"],"kos":r["kos"],"trainers":r["trainers"],"extra":r["extra"]}) for r in items],
        key=lambda kv:(kv[1]["trainers"], kv[1]["kos"], kv[1]["rounds"], kv[1]["extra"]),
        reverse=True
    )
    return rows

@app.route("/")
def home(): return redirect(url_for("board_pc"))

@app.route("/pc")
def board_pc():
    with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
        rows = _rows_from(cur, "scores")
    return render_template_string(TPL_BASE, title="BXH Pokémon Việt Nam — PC", rows=rows, show_upload=False)

@app.route("/android")
def board_android():
    with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
        rows = _rows_from(cur, "android_scores")
    return render_template_string(TPL_BASE, title="BXH Pokémon Việt Nam — Android", rows=rows, show_upload=True)

# static (nếu dùng)
@app.route("/static/<path:fname>")
def static_files(fname):
    return send_from_directory("static", fname)

# tiện cho bạn reset mỗi bảng
@app.route("/api/clear_pc", methods=["POST"])
def clear_pc():
    if (request.form.get("token") or "") != TOKEN: return jsonify(error="bad token"), 401
    with db_conn() as con, con.cursor() as cur: cur.execute("TRUNCATE TABLE scores")
    return jsonify(ok=True)

@app.route("/api/clear_android", methods=["POST"])
def clear_android():
    if (request.form.get("token") or "") != TOKEN: return jsonify(error="bad token"), 401
    with db_conn() as con, con.cursor() as cur: cur.execute("TRUNCATE TABLE android_scores")
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
