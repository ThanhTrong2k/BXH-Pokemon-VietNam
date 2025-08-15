from flask import Flask, request, jsonify, render_template_string, redirect, url_for, send_from_directory
import os, json, hmac, hashlib, base64, time, threading, traceback, sys
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
LOCK = threading.Lock()

# ========= CONFIG =========
TOKEN        = os.environ.get("API_TOKEN", "POKEMONVIETNAM")         # PC API token
DATABASE_URL = os.environ.get("DATABASE_URL")                        # Neon
UPLOAD_KEY   = (os.environ.get("UPLOAD_KEY") or "POKEMONVIETNAM")    # = SECRET trong game

def log(msg): print(msg); sys.stdout.flush()

# ========= DB =========
def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL")
    return psycopg.connect(DATABASE_URL, autocommit=True)

def init_db():
    with db_conn() as con, con.cursor() as cur:
        # PC
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
        # Android
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

# ========= PC API =========
@app.route("/api/report", methods=["POST"])
def report_pc():
    try:
        data = request.form.to_dict() or (request.get_json(silent=True) or {})
        if not data:                 return jsonify(error="no data"), 400
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

# ========= ANDROID UPLOAD (bytes-safe) =========
def _parse_bxh_file(raw_bytes):
    """Nhận .bxh base64(JSON) hoặc JSON thô -> dict (giữ nguyên bytes)."""
    try:
        raw_bytes = base64.b64decode(raw_bytes, validate=True)
    except Exception:
        pass
    text = raw_bytes.decode("latin-1", "ignore")  # 1:1 bytes
    return json.loads(text)

def _msg_bytes(p):
    # "#{uid}|#{name}|#{action}|#{rounds}|#{kos}|#{trainers}|#{extra}|#{ts}"
    parts = [
        str(p.get("uid","")).encode("ascii", "ignore"),
        str(p.get("name","")).encode("latin-1", "ignore"),  # tên giữ nguyên bytes
        str(p.get("action","")).encode("ascii", "ignore"),
        str(int(p.get("rounds",0))).encode("ascii"),
        str(int(p.get("kos",0))).encode("ascii"),
        str(int(p.get("trainers",0))).encode("ascii"),
        str(int(p.get("extra",0))).encode("ascii"),
        str(int(p.get("ts",0))).encode("ascii"),
    ]
    return b"|".join(parts)

def _calc_sig(p):
    alg = (p.get("alg") or "sha1").lower()
    digest = hashlib.sha1 if alg == "sha1" else hashlib.sha256
    return hmac.new(UPLOAD_KEY.encode("ascii"), _msg_bytes(p), digest).hexdigest()

@app.route("/api/upload_android", methods=["POST"])
def upload_android():
    try:
        f = request.files.get("file")
        if not f: return jsonify(error="no file"), 400
        data = _parse_bxh_file(f.read())

        # Verify
        sig_client = str(data.get("sig",""))
        if not sig_client: return jsonify(error="missing sig"), 400
        sig_server = _calc_sig(data)
        if not hmac.compare_digest(sig_client, sig_server):
            return jsonify(error="bad signature"), 401

        uid      = str(data.get("uid") or "").strip()
        name     = (data.get("name") or "Unknown").strip()[:40]
        action   = (data.get("action") or "delta").lower()
        rounds   = int(str(data.get("rounds") or 0))
        kos      = int(str(data.get("kos") or 0))
        trainers = int(str(data.get("trainers") or 0))
        extra    = int(str(data.get("extra") or 0))
        ts       = int(str(data.get("ts") or 0))

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

        # Thành công -> chuyển ngay về BXH all
        return redirect(url_for("board_all"), code=303)
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
/* Cột số: canh giữa + tabular-nums cho thẳng hàng */
th.num, td.num{
  text-align: center;
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum" 1, "lnum" 1;
}
td.name{ text-align: left; }   /* tên vẫn canh trái */
td.rank{ text-align: center; } /* thứ hạng canh giữa */
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
/* ==== Header responsive (mobile đẹp hơn) ==== */
.headerbar{ display:grid; grid-template-columns: 1fr auto; align-items:center; gap:12px; overflow:visible; }
.header-title{ min-width: 220px; }
.header-actions{ display:flex; flex-wrap:wrap; align-items:center; justify-content:flex-end; gap:8px; }
.header-actions .row{ display:flex; flex-wrap:wrap; gap:8px; }
.header-actions input[type="file"], .header-actions input[type="text"], .header-actions select, .header-actions button{ max-width:100%; }
@media (max-width: 880px){ .headerbar{ grid-template-columns: 1fr; } .header-actions{ justify-content:flex-start; } }
@media (max-width: 560px){ .header-actions .row{ flex-direction:column; width:100%; } .header-actions input[type="file"], .header-actions input[type="text"], .header-actions select, .header-actions button{ width:100%; } }
</style>
<div class="container">
  <div class="card">
    <div class="header headerbar">
      <div class="header-title">
        <div class="h1">{{ title }}</div>
      </div>

      <div class="header-actions">
        <!-- Hàng 1: Upload -->
        <div class="row">
          {% if show_upload %}
          <form class="upload-pill" method="post" action="/api/upload_android" enctype="multipart/form-data">
            <input type="file" name="file" id="bxhFile" accept=".bxh,.json,.txt" required>
            <button id="btnUpload" type="submit">↑ Upload file</button>
          </form>
          {% endif %}
        </div>
        <!-- Hàng 2: Tìm kiếm + Lọc + Tải lại -->
        <div class="row">
          <input id="q" type="search" placeholder="Tìm người chơi…">
          <select id="sortBy">
            <option value="default">Mặc định (T↓ K↓ R↓ E↓)</option>
            <option value="kos">KOs cao nhất</option>
            <option value="rounds">Rounds cao nhất</option>
            <option value="extra">Extra cao nhất</option>
          </select>
          <button id="btnReload" onclick="location.reload()">↻ Tải lại</button>
        </div>
      </div>
    </div>
    <div class="table-wrap">
      <table id="board">
      <colgroup>
          <col style="width:72px">   <!-- # -->
          <col>                       <!-- Tên (auto) -->
          <col style="width:110px">   <!-- Rounds -->
          <col style="width:110px">   <!-- KOs -->
          <col style="width:120px">   <!-- Trainers -->
          <col style="width:100px">   <!-- Extra -->
      </colgroup>
      <thead>
        <tr>
          <th>#</th>
          <th>Tên</th>
          <th class="num">Rounds</th>
          <th class="num">KOs</th>
          <th class="num">Trainers</th>
          <th class="num">Extra</th>
        </tr>
      </thead>
        <tbody>
        {% for name, row in rows %}
          <tr data-name="{{ name|lower }}" data-rounds="{{ row.rounds }}" data-kos="{{ row.kos }}" data-trainers="{{ row.trainers }}" data-extra="{{ row.extra }}">
            <td class="rank"><span class="badge">{{ loop.index }}</span></td>
            <td class="name">{{ name }}</td>
            <td class="num">{{ row.rounds }}</td>
            <td class="num">{{ row.kos }}</td>
            <td class="num">{{ row.trainers }}</td>
            <td class="num">{{ row.extra }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    <div class="footer"><span>Hiển thị {{ rows|length }} người chơi</span><span id="updatedAt">⏱️</span></div>
  </div>
</div>
<script>
const q=document.getElementById('q');
q?.addEventListener('input',()=>{const t=q.value.toLowerCase();document.querySelectorAll('#board tbody tr').forEach(tr=>{tr.style.display=tr.dataset.name.includes(t)?'':'none';});});
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

# Gộp PC + Android
def _rows_all(cur):
    cur.execute("""
      WITH u AS (
        SELECT name, rounds, kos, trainers, extra FROM scores
        UNION ALL
        SELECT name, rounds, kos, trainers, extra FROM android_scores
      )
      SELECT name,
             SUM(rounds)   AS rounds,
             SUM(kos)      AS kos,
             SUM(trainers) AS trainers,
             SUM(extra)    AS extra
      FROM u
      GROUP BY name
    """)
    items = cur.fetchall()
    rows = sorted(
        [(r["name"], {
            "rounds":   int(r["rounds"] or 0),
            "kos":      int(r["kos"] or 0),
            "trainers": int(r["trainers"] or 0),
            "extra":    int(r["extra"] or 0),
        }) for r in items],
        key=lambda kv: (kv[1]["trainers"], kv[1]["kos"], kv[1]["rounds"], kv[1]["extra"]),
        reverse=True
    )
    return rows

@app.route("/")
def home():
    return redirect(url_for("board_all"))

@app.route("/all")
def board_all():
    with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
        rows = _rows_all(cur)
    return render_template_string(TPL_BASE, title="BXH Pokémon Việt Nam — ALL", rows=rows, show_upload=True)

# Giữ route cũ nhưng chuyển hướng để không nhầm
@app.route("/pc")
def board_pc_redirect():
    return redirect(url_for("board_all"))

@app.route("/android")
def board_android_redirect():
    return redirect(url_for("board_all"))

# static (nếu dùng)
@app.route("/static/<path:fname>")
def static_files(fname):
    return send_from_directory("static", fname)

# tiện reset từng bảng (PC/Android)
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


