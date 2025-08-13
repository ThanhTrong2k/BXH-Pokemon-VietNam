from flask import Flask, request, jsonify, render_template_string, send_from_directory
import json, os, threading

app = Flask(__name__)
LOCK = threading.Lock()

# Cho phép cấu hình nơi lưu dữ liệu (Render Disk)
DB_PATH = os.environ.get("DB_PATH", "db.json")
TOKEN   = os.environ.get("API_TOKEN", "POKEMONVIETNAM")

# ===== utils =====
def load_db():
    if os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_db(db):
    with LOCK:
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)

# ===== API =====
@app.route("/api/health")
def health():
    return jsonify(ok=True)

@app.route("/api/report", methods=["POST"])
def report():
    # nhận form-urlencoded hoặc JSON
    data = request.form.to_dict() or (request.get_json(silent=True) or {})
    if not data:
        return jsonify(error="no data"), 400
    if data.get("token") != TOKEN:
        return jsonify(error="bad token"), 401

    action   = (data.get("action") or "set").lower()
    name     = (data.get("name")   or "Unknown").strip()[:40]
    rounds   = int(data.get("rounds")   or 0)
    kos      = int(data.get("kos")      or 0)
    trainers = int(data.get("trainers") or 0)
    extra    = int(data.get("extra")    or 0)

    db  = load_db()
    row = db.get(name, {"rounds":0,"kos":0,"trainers":0,"extra":0})

    if action == "delta":
        row["rounds"]   += rounds
        row["kos"]      += kos
        row["trainers"] += trainers
        row["extra"]    += extra
    else:  # set
        row["rounds"]   = rounds
        row["kos"]      = kos
        row["trainers"] = trainers
        row["extra"]    = extra

    db[name] = row
    save_db(db)
    return jsonify(ok=True, name=name, **row)

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

# (tuỳ chọn) phục vụ file tĩnh nếu bạn có /static
@app.route("/static/<path:fname>")
def static_files(fname):
    return send_from_directory("static", fname)
# --- Xem DB thô ---
@app.route("/api/raw")
def raw():
    return jsonify(load_db())

# --- Reset DB (chỉ cho chủ) ---
@app.route("/api/clear", methods=["POST"])
def clear():
    if (request.form.get("token") or "") != TOKEN:
        return jsonify(error="bad token"), 401
    save_db({})
    return jsonify(ok=True)

# --- Form gửi tay để test ---
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


