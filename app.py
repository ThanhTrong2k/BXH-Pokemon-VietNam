# ==== imports ====
import uuid
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, make_response, jsonify
import os
import re
from datetime import datetime

from flask_babel import Babel, gettext as _, get_locale
from werkzeug.utils import secure_filename
from PIL import Image

# ---- Postgres optional (Neon) ----
USING_POSTGRES = bool(os.getenv("DATABASE_URL"))
if USING_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
# ==================================

API_TOKEN = os.getenv("API_TOKEN", "")
DISABLE_SEED = os.getenv("DISABLE_SEED", "0") == "1"

# ==== Flask app ====
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# ---- Babel (API mới) ----
app.config['BABEL_DEFAULT_LOCALE'] = 'vi'
app.config['BABEL_DEFAULT_TIMEZONE'] = 'Asia/Ho_Chi_Minh'
app.config['BABEL_SUPPORTED_LOCALES'] = ['vi', 'en']

babel = Babel()

def select_locale():
    # Ưu tiên ?lang=... > cookie > Accept-Language
    lang = request.args.get('lang')
    if lang in app.config['BABEL_SUPPORTED_LOCALES']:
        return lang
    cookie_lang = request.cookies.get('lang')
    if cookie_lang in app.config['BABEL_SUPPORTED_LOCALES']:
        return cookie_lang
    return request.accept_languages.best_match(app.config['BABEL_SUPPORTED_LOCALES'])

def select_timezone():
    return app.config['BABEL_DEFAULT_TIMEZONE']

babel.init_app(app, locale_selector=select_locale, timezone_selector=select_timezone)

# expose get_locale() cho Jinja (string)
app.jinja_env.globals['get_locale'] = lambda: str(get_locale())
# ==================================


# ==== Upload config (ephemeral trên PaaS) ====
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['THUMB_FOLDER']  = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbs')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['THUMB_FOLDER'],  exist_ok=True)
ALLOWED_EXTS = {'png', 'jpg', 'jpeg', 'webp'}

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTS

def make_thumb(src_path: str, dst_path: str, size=(48, 48)):
    with Image.open(src_path) as im:
        im = im.convert("RGBA")
        im.thumbnail(size)
        im.save(dst_path)
# ==============================================


# ==== DB: SQLite (local) hoặc Postgres (Neon) ====
DB_PATH = os.getenv('DB_PATH', os.path.join(app.root_path, 'leaderboard.db'))

def get_db():
    if USING_POSTGRES:
        dsn = os.environ["DATABASE_URL"]
        if "connect_timeout" not in dsn:
            dsn = dsn + ("&" if "?" in dsn else "?") + "connect_timeout=10"
        return psycopg.connect(dsn, row_factory=dict_row)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    if USING_POSTGRES:
        with get_db() as con, con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS leaderboard (
                    id BIGSERIAL PRIMARY KEY,
                    rank INTEGER,
                    player TEXT NOT NULL,
                    trainer INTEGER DEFAULT 0,
                    rounds INTEGER DEFAULT 0,
                    kos INTEGER DEFAULT 0,
                    team TEXT DEFAULT '',
                    team_img TEXT,
                    updated_at TEXT
                )
            """)
            # unique theo player (không phân biệt hoa/thường)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS leaderboard_player_lower_idx
                ON leaderboard (LOWER(player));
            """)
            con.commit()
    else:
        with get_db() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS leaderboard (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rank INTEGER,
                    player TEXT UNIQUE COLLATE NOCASE,
                    trainer INTEGER DEFAULT 0,
                    rounds INTEGER DEFAULT 0,
                    kos INTEGER DEFAULT 0,
                    team TEXT DEFAULT '',
                    team_img TEXT,
                    updated_at TEXT
                )
            """)

def seed_if_empty():
    if USING_POSTGRES:
        with get_db() as con, con.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM leaderboard")
            c = cur.fetchone()["c"]
            if c == 0:
                cur.executemany("""
                    INSERT INTO leaderboard (rank, player, trainer, rounds, kos, team, team_img, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, [
                    (1,"Trong",8,6,1,"Charizard, Gardevoir, Swampert",None,datetime.utcnow().isoformat()),
                    (2,"Minh",7,5,0,"Blaziken, Metagross, Milotic",None,datetime.utcnow().isoformat()),
                    (3,"Lan",6,4,0,"Lucario, Tyranitar, Jolteon",None,datetime.utcnow().isoformat()),
                ])
                con.commit()
    else:
        with get_db() as con:
            c = con.execute("SELECT COUNT(*) AS c FROM leaderboard").fetchone()["c"]
            if c == 0:
                con.executemany("""
                    INSERT INTO leaderboard (rank, player, trainer, rounds, kos, team, team_img, updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, [
                    (1,"Trong",8,6,1,"Charizard, Gardevoir, Swampert",None,datetime.utcnow().isoformat()),
                    (2,"Minh",7,5,0,"Blaziken, Metagross, Milotic",None,datetime.utcnow().isoformat()),
                    (3,"Lan",6,4,0,"Lucario, Tyranitar, Jolteon",None,datetime.utcnow().isoformat()),
                ])

# tạo bảng + seed ngay khi app khởi động
init_db()
if not DISABLE_SEED:
    seed_if_empty()
# ==================================================
def _corsify(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    return resp
    
# ==== Routes ====
@app.route("/api/report", methods=["OPTIONS"])
def api_report_preflight():
    return _corsify(make_response("", 204))

@app.route("/")
def home():
    return redirect(url_for('board'))

@app.route("/set-language/<lang_code>")
def set_language(lang_code):
    if lang_code not in app.config['BABEL_SUPPORTED_LOCALES']:
        lang_code = app.config['BABEL_DEFAULT_LOCALE']
    resp = make_response(redirect(request.referrer or url_for('board')))
    resp.set_cookie("lang", lang_code, max_age=60*60*24*365)  # 1 năm
    return resp


@app.route("/board")
def board():
    # --- lấy số liệu tổng + top 3 ---
    if USING_POSTGRES:
        with get_db() as con, con.cursor() as cur:
            cur.execute("""
                SELECT
                  COUNT(*)                    AS players,
                  COALESCE(SUM(rounds), 0)    AS total_rounds,
                  COALESCE(SUM(kos), 0)       AS total_kos,
                  COALESCE(SUM(trainer), 0)   AS total_trainers
                FROM leaderboard
            """)
            stats = cur.fetchone()

            cur.execute("""
                SELECT rank, player, trainer, rounds, kos, team, team_img, updated_at
                FROM leaderboard
                ORDER BY COALESCE(updated_at,'1970-01-01') DESC, rank ASC
                LIMIT 3
            """)
            top3_rows = cur.fetchall()

            cur.execute("""
                SELECT rank, player, trainer, rounds, kos, team, team_img
                FROM leaderboard
                ORDER BY rank ASC, player ASC
            """)
            rows = cur.fetchall()
    else:
        with get_db() as con:
            stats = con.execute("""
                SELECT
                  COUNT(*)                    AS players,
                  COALESCE(SUM(rounds), 0)    AS total_rounds,
                  COALESCE(SUM(kos), 0)       AS total_kos,
                  COALESCE(SUM(trainer), 0)   AS total_trainers
                FROM leaderboard
            """).fetchone()

            top3_rows = con.execute("""
                SELECT rank, player, trainer, rounds, kos, team, team_img, updated_at
                FROM leaderboard
                ORDER BY COALESCE(updated_at,'1970-01-01') DESC, rank ASC
                LIMIT 3
            """).fetchall()

            rows = con.execute("""
                SELECT rank, player, trainer, rounds, kos, team, team_img
                FROM leaderboard
                ORDER BY rank ASC, player ASC
            """).fetchall()

    # --- gắn URL ảnh nếu file còn tồn tại ---
    def attach_img_urls(r):
        d = dict(r)
        d["team_thumb_url"] = None
        d["team_img_url"] = None
        fn = d.get("team_img")
        if fn:
            full_path = os.path.join(app.config['UPLOAD_FOLDER'], fn)
            if os.path.isfile(full_path):
                d["team_img_url"] = url_for('static', filename='uploads/' + fn)
                d["team_thumb_url"] = url_for('static', filename='uploads/thumbs/' + fn)
        return d

    leaderboard = [attach_img_urls(r) for r in rows]
    top3 = [attach_img_urls(r) for r in top3_rows]

    players_count = int(stats["players"])
    total_rounds = int(stats["total_rounds"] or 0)
    total_kos       = int(stats["total_kos"] or 0)
    total_trainers  = int(stats["total_trainers"] or 0)

    return render_template(
        "board_babel.html",
        leaderboard=leaderboard,
        players_count=players_count,
        total_rounds=total_rounds,
        total_kos=total_kos,
        total_trainers=total_trainers,
        top3=top3,
    )

@app.post("/upload")
def upload_team_image():
    lang = request.args.get('lang')
    player = (request.form.get('player') or '').strip() or 'Unknown'

    # Parse team names (REQUIRED 1..6)
    raw_team = (request.form.get('team_names') or '').strip()
    names = [re.sub(r'\s+', ' ', s.strip()) for s in re.split(r'[,\n;]+', raw_team) if s.strip()]
    if len(names) == 0 or len(names) > 6:
        return redirect(url_for('board', lang=lang))
    team_text = ', '.join(names)

    # Image OPTIONAL
    file = request.files.get('image')
    fname = None
    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        safe = secure_filename(os.path.splitext(file.filename)[0])[:32]
        fname = f"{safe}-{uuid.uuid4().hex[:8]}.{ext}"

        save_path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
        file.save(save_path)
        try:
            thumb_path = os.path.join(app.config['THUMB_FOLDER'], fname)
            make_thumb(save_path, thumb_path, size=(48, 48))
        except Exception:
            pass

    now = datetime.utcnow().isoformat()

    if USING_POSTGRES:
        with get_db() as con, con.cursor() as cur:
            cur.execute("SELECT id FROM leaderboard WHERE player ILIKE %s", (player,))
            row = cur.fetchone()
            if row:
                if fname:
                    cur.execute("UPDATE leaderboard SET team=%s, team_img=%s, updated_at=%s WHERE id=%s",
                                (team_text, fname, now, row["id"]))
                else:
                    cur.execute("UPDATE leaderboard SET team=%s, updated_at=%s WHERE id=%s",
                                (team_text, now, row["id"]))
            else:
                cur.execute("SELECT COALESCE(MAX(rank),0)+1 AS n FROM leaderboard")
                next_rank = cur.fetchone()["n"]
                if fname:
                    cur.execute("""INSERT INTO leaderboard (rank, player, team, team_img, updated_at)
                                   VALUES (%s,%s,%s,%s,%s)""",
                                (next_rank, player, team_text, fname, now))
                else:
                    cur.execute("""INSERT INTO leaderboard (rank, player, team, updated_at)
                                   VALUES (%s,%s,%s,%s)""",
                                (next_rank, player, team_text, now))
            con.commit()
    else:
        with get_db() as con:
            row = con.execute("SELECT id FROM leaderboard WHERE player = ? COLLATE NOCASE", (player,)).fetchone()
            if row:
                if fname:
                    con.execute("UPDATE leaderboard SET team=?, team_img=?, updated_at=? WHERE id=?",
                                (team_text, fname, now, row["id"]))
                else:
                    con.execute("UPDATE leaderboard SET team=?, updated_at=? WHERE id=?",
                                (team_text, now, row["id"]))
            else:
                next_rank = con.execute("SELECT COALESCE(MAX(rank),0)+1 AS n FROM leaderboard").fetchone()["n"]
                if fname:
                    con.execute("""INSERT INTO leaderboard (rank, player, team, team_img, updated_at)
                                   VALUES (?,?,?,?,?)""",
                                (next_rank, player, team_text, fname, now))
                else:
                    con.execute("""INSERT INTO leaderboard (rank, player, team, updated_at)
                                   VALUES (?,?,?,?)""",
                                (next_rank, player, team_text, now))

    return redirect(url_for('board', lang=lang))
# ==================

@app.post("/api/report")
def api_report():
    # ---- auth bằng token ----
    token = (request.headers.get('Authorization') or '').replace('Bearer ', '').strip()
    if not token and request.is_json:
        token = (request.get_json(silent=True) or {}).get('token', '')
    if not token and request.form:
        token = request.form.get('token', '')
    if API_TOKEN and token != API_TOKEN:
        return _corsify(jsonify({"ok": False, "error": "unauthorized"})), 401

    # ---- đọc data (JSON hoặc form) ----
    data = request.get_json(silent=True) or request.form.to_dict()

    player = (data.get('player') or '').strip()
    if not player:
        return _corsify(jsonify({"ok": False, "error": "player required"})), 400

    def to_int(x):
        try:
            return int(x)
        except Exception:
            return None

    # set tuyệt đối
    rounds  = to_int(data.get('rounds'))
    kos     = to_int(data.get('kos'))
    trainer = to_int(data.get('trainer'))

    # cộng dồn
    d_rounds  = to_int(data.get('delta_rounds'))
    d_kos     = to_int(data.get('delta_kos'))
    d_trainer = to_int(data.get('delta_trainer'))

    # team names: "A, B, C" (≤6)
    team_text = None
    raw_team = (data.get('team_names') or '').strip()
    if raw_team:
        names = [re.sub(r'\s+', ' ', s.strip()) for s in re.split(r'[,\n;]+', raw_team) if s.strip()]
        if len(names) > 6:
            names = names[:6]
        team_text = ', '.join(names)

    now = datetime.utcnow().isoformat()

    # ---- upsert vào DB ----
    if USING_POSTGRES:
        with get_db() as con, con.cursor() as cur:
            cur.execute("SELECT id, rounds, kos, trainer FROM leaderboard WHERE player ILIKE %s", (player,))
            row = cur.fetchone()
            if row:
                new_rounds  = row['rounds']  or 0
                new_kos     = row['kos']     or 0
                new_trainer = row['trainer'] or 0

                if d_rounds  is not None: new_rounds  += d_rounds
                if d_kos     is not None: new_kos     += d_kos
                if d_trainer is not None: new_trainer += d_trainer
                if rounds  is not None: new_rounds  = rounds
                if kos     is not None: new_kos     = kos
                if trainer is not None: new_trainer = trainer

                if team_text is not None:
                    cur.execute("""UPDATE leaderboard
                                   SET rounds=%s, kos=%s, trainer=%s, team=%s, updated_at=%s
                                   WHERE id=%s""",
                                (new_rounds, new_kos, new_trainer, team_text, now, row['id']))
                else:
                    cur.execute("""UPDATE leaderboard
                                   SET rounds=%s, kos=%s, trainer=%s, updated_at=%s
                                   WHERE id=%s""",
                                (new_rounds, new_kos, new_trainer, now, row['id']))
            else:
                cur.execute("SELECT COALESCE(MAX(rank),0)+1 AS n FROM leaderboard")
                next_rank = cur.fetchone()["n"]
                cur.execute("""INSERT INTO leaderboard (rank, player, rounds, kos, trainer, team, updated_at)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                            (next_rank, player,
                             rounds or d_rounds or 0,
                             kos or d_kos or 0,
                             trainer or d_trainer or 0,
                             team_text, now))
            con.commit()
    else:
        with get_db() as con:
            row = con.execute("SELECT id, rounds, kos, trainer FROM leaderboard WHERE player = ? COLLATE NOCASE",
                              (player,)).fetchone()
            if row:
                new_rounds  = row['rounds']  or 0
                new_kos     = row['kos']     or 0
                new_trainer = row['trainer'] or 0

                if d_rounds  is not None: new_rounds  += d_rounds
                if d_kos     is not None: new_kos     += d_kos
                if d_trainer is not None: new_trainer += d_trainer
                if rounds  is not None: new_rounds  = rounds
                if kos     is not None: new_kos     = kos
                if trainer is not None: new_trainer = trainer

                if team_text is not None:
                    con.execute("""UPDATE leaderboard
                                   SET rounds=?, kos=?, trainer=?, team=?, updated_at=?
                                   WHERE id=?""",
                                (new_rounds, new_kos, new_trainer, team_text, now, row['id']))
                else:
                    con.execute("""UPDATE leaderboard
                                   SET rounds=?, kos=?, trainer=?, updated_at=?
                                   WHERE id=?""",
                                (new_rounds, new_kos, new_trainer, now, row['id']))
            else:
                next_rank = con.execute("SELECT COALESCE(MAX(rank),0)+1 AS n FROM leaderboard").fetchone()["n"]
                con.execute("""INSERT INTO leaderboard (rank, player, rounds, kos, trainer, team, updated_at)
                               VALUES (?,?,?,?,?,?,?)""",
                            (next_rank, player,
                             rounds or d_rounds or 0,
                             kos or d_kos or 0,
                             trainer or d_trainer or 0,
                             team_text, now))

    return _corsify(jsonify({"ok": True})), 200

@app.get("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    # Local dev
    app.run(debug=True)




