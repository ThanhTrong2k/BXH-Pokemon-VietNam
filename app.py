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
          uid        TEXT PRIMARY KEY,
          name       TEXT NOT NULL,
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

        if not data:                         return jsonify(error="no data"), 400
        if data.get("token") != TOKEN:       return jsonify(error="bad token"), 401

        action   = (data.get("action") or "set").lower()
        name     = (data.get("name")   or "Unknown").strip()[:40]
        uid      = (data.get("uid")    or "").strip()
        if not uid:
            uid = "__name__:" + name  # tương thích game cũ

        rounds   = safe_int(data.get("rounds"))
        kos      = safe_int(data.get("kos"))
        trainers = safe_int(data.get("trainers"))
        extra    = safe_int(data.get("extra"))

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
  --bg:#0e111a; --fg:#e8ebf7; --card:#101627; --accent:#ffcc00; --accent2:#3b4cca;
  --muted:#a7aec6; --border:#20273a; --row:#0f1424;
}
*{box-sizing:border-box}
body{
  margin:0;
  background:
    radial-gradient(1200px 800px at 10% -10%, rgba(255,204,0,.06), transparent),
    radial-gradient(900px 700px at 110% 10%, rgba(59,76,202,.10), transparent),
    var(--bg);
  color:var(--fg);
  font:500 16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Helvetica Neue,Arial,Noto Sans,sans-serif;
}
.container{max-width:1060px;margin:24px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;
      box-shadow:0 10px 30px rgba(0,0,0,.25);overflow:hidden}
.header{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--border);
        background:linear-gradient(to right, rgba(255,204,0,.10), rgba(59,76,202,.10))}
.left{display:flex;align-items:center;gap:12px;flex:1}
.logo{width:28px;height:28px;display:inline-block}
.h1{font-size:20px;font-weight:800;letter-spacing:.3px}
.sprites{display:flex;gap:6px;opacity:.9}
.sprites img{width:28px;height:28px;image-rendering:pixelated;filter: drop-shadow(0 2px 2px rgba(0,0,0,.35));}
.controls{display:flex;gap:8px;align-items:center}
input[type=search], select, button{
  appearance:none;border:1px solid var(--border);background:#0b1222;color:var(--fg);
  padding:8px 10px;border-radius:10px
}
button{cursor:pointer}
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 8px;border-radius:999px;border:1px solid var(--border);
       font-variant-numeric:tabular-nums;background:#0b1222}
.medal-1{background:linear-gradient(180deg,#fff3bf,#ffe066);color:#111;border-color:#ffd43b}
.medal-2{background:linear-gradient(180deg,#f1f3f5,#dee2e6);color:#111;border-color:#adb5bd}
.medal-3{background:linear-gradient(180deg,#ffe8cc,#ffc078);color:#111;border-color:#ffa94d}
.table-wrap{overflow:auto}
table{width:100%;border-collapse:separate;border-spacing:0}
th,td{padding:12px 14px;border-bottom:1px solid var(--border);white-space:nowrap}
th{position:sticky;top:0;background:var(--card);z-index:1;font-size:13px;color:var(--muted);
   text-transform:uppercase;letter-spacing:.08em}
tbody tr:nth-child(even){background:var(--row)}
.rank{text-align:center;font-weight:800}
.name{font-weight:700}
.footer{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;color:var(--muted);font-size:13px}
.music{display:flex;align-items:center;gap:6px}
.music .title{max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:13px}
@media (max-width:720px){
  .h1{font-size:18px}
  th,td{padding:10px 12px}
  .controls{flex-wrap:wrap;justify-content:flex-end}
}
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <div class="header">
      <div class="left">
        <svg class="logo" viewBox="0 0 32 32" fill="none" aria-hidden="true">
          <circle cx="16" cy="16" r="14" stroke="var(--accent2)" stroke-width="4"/>
          <path d="M2 16h28" stroke="var(--fg)" stroke-width="4"/>
          <circle cx="16" cy="16" r="5" fill="var(--accent)" stroke="var(--fg)" stroke-width="2"/>
        </svg>
        <div class="h1">BXH Pokémon Việt Nam</div>
        <div class="sprites" aria-hidden="true">
          <img src="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/25.png" alt="">
          <img src="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/6.png" alt="">
          <img src="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/3.png" alt="">
        </div>
      </div>
      <div class="controls">
        <input id="q" type="search" placeholder="Tìm người chơi…">
        <select id="sortBy" title="Sắp xếp">
          <option value="default">Mặc định (T↓ K↓ R↓ E↓)</option>
          <option value="kos">KOs cao nhất</option>
          <option value="rounds">Rounds cao nhất</option>
          <option value="extra">Extra cao nhất</option>
        </select>
        <button onclick="location.reload()">↻ Tải lại</button>
        {% if tracks|length > 0 %}
        <div class="music">
          <button id="btnPlay" title="Phát/Tạm dừng">♫ Play</button>
          <button id="btnNext" title="Bài ngẫu nhiên tiếp theo">⏭</button>
          <span class="title" id="songTitle"></span>
          <audio id="player" preload="none" crossorigin="anonymous"></audio>
          <script>
            const tracks = {{ tracks|tojson }};
          </script>
        </div>
        {% endif %}
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
          <tr data-name="{{ name|lower }}"
              data-rounds="{{ row.rounds }}"
              data-kos="{{ row.kos }}"
              data-trainers="{{ row.trainers }}"
              data-extra="{{ row.extra }}">
            <td class="rank">
              {% if loop.index==1 %}<span class="badge medal-1">🥇 {{loop.index}}</span>
              {% elif loop.index==2 %}<span class="badge medal-2">🥈 {{loop.index}}</span>
              {% elif loop.index==3 %}<span class="badge medal-3">🥉 {{loop.index}}</span>
              {% else %}<span class="badge">{{loop.index}}</span>{% endif %}
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
// ---- Filter theo tên ----
const q = document.getElementById('q');
q?.addEventListener('input', () => {
  const term = q.value.toLowerCase();
  document.querySelectorAll('#board tbody tr').forEach(tr => {
    tr.style.display = tr.dataset.name.includes(term) ? '' : 'none';
  });
});

// ---- Sort client-side ----
const sortBy = document.getElementById('sortBy');
function sortTable(mode){
  const tbody = document.querySelector('#board tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const keyDefault = r => [+(r.dataset.trainers||0), +(r.dataset.kos||0), +(r.dataset.rounds||0), +(r.dataset.extra||0)];
  const keyKos     = r => [+(r.dataset.kos||0), +(r.dataset.trainers||0), +(r.dataset.rounds||0), +(r.dataset.extra||0)];
  const keyRounds  = r => [+(r.dataset.rounds||0), +(r.dataset.trainers||0), +(r.dataset.kos||0), +(r.dataset.extra||0)];
  const keyExtra   = r => [+(r.dataset.extra||0), +(r.dataset.trainers||0), +(r.dataset.kos||0), +(r.dataset.rounds||0)];
  const getKey = (r) => (mode==='kos'?keyKos(r):mode==='rounds'?keyRounds(r):mode==='extra'?keyExtra(r):keyDefault(r));
  rows.sort((a,b) => {
    const ka=getKey(a), kb=getKey(b);
    for(let i=0;i<ka.length;i++){ if(kb[i]!==ka[i]) return kb[i]-ka[i]; }
    return a.dataset.name.localeCompare(b.dataset.name);
  });
  rows.forEach((r,i)=>{
    // cập nhật huy chương & thứ hạng
    const cell = r.querySelector('.rank');
    const rank = i+1;
    const medal = rank===1?'🥇':rank===2?'🥈':rank===3?'🥉':'';
    cell.innerHTML = medal
      ? `<span class="badge medal-${rank}">${medal} ${rank}</span>`
      : `<span class="badge">${rank}</span>`;
    tbody.appendChild(r);
  });
}
sortBy?.addEventListener('change', ()=>sortTable(sortBy.value));

// ---- Nhạc (chỉ khi có tracks & user gesture) ----
(function(){
  const player = document.getElementById('player');
  if(!player) return;
  const title = document.getElementById('songTitle');
  const btnPlay = document.getElementById('btnPlay');
  const btnNext = document.getElementById('btnNext');
  let idx = Math.floor(Math.random()*tracks.length);

  function setSrc(i){
    idx = (i+tracks.length)%tracks.length;
    player.src = tracks[idx];
    title.textContent = tracks[idx].split('/').pop();
  }
  function next(){ setSrc(idx+1); player.play().catch(()=>{}); }

  setSrc(idx);

  btnPlay.addEventListener('click', ()=>{
    if(player.paused){ player.play().catch(()=>{}); btnPlay.textContent = "⏸ Pause"; }
    else{ player.pause(); btnPlay.textContent = "♫ Play"; }
  });
  btnNext.addEventListener('click', next);
  player.addEventListener('ended', next);
})();
</script>
</body>
</html>
"""


from datetime import datetime
import os, re

def short_code(uid):
    if uid.startswith("__name__:"):  # bản cũ
        return ""
    # lấy 4 ký tự cuối chữ-số làm “mã”
    s = re.sub(r"[^A-Z0-9]", "", uid.upper())
    return s[-4:] if len(s) >= 4 else s

@app.route("/")
@app.route("/board")
def board():
    with db_conn() as con, con.cursor(row_factory=dict_row) as cur:
        cur.execute("""
          SELECT uid, name, rounds, kos, trainers, extra
          FROM scores
          ORDER BY trainers DESC, kos DESC, rounds DESC, extra DESC, updated_at DESC
        """)
        recs = cur.fetchall()

    # hiển thị "Tên · CODE" nếu có uid thật
    rows = []
    for r in recs:
        code = short_code(r["uid"])
        display = r["name"] + (f" · {code}" if code else "")
        rows.append((display, {"rounds":r["rounds"],"kos":r["kos"],"trainers":r["trainers"],"extra":r["extra"]}))

    # nhạc trong /static/bgm (giữ như bạn đã cài)
    tracks = []
    try:
        base = os.path.join(os.path.dirname(__file__), "static", "bgm")
        for f in os.listdir(base):
            if f.lower().endswith((".mp3",".ogg",".m4a",".wav")):
                tracks.append(f"/static/bgm/{f}")
    except: pass

    now = datetime.now().strftime("%H:%M:%S")
    return render_template_string(TPL, rows=rows, updated_at=now, tracks=tracks)

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



