# ==== imports ====
import os
import uuid
import sqlite3
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, make_response
from flask_babel import Babel, gettext as _, get_locale
from werkzeug.utils import secure_filename
from PIL import Image

# ---- Postgres optional (Neon) ----
USING_POSTGRES = bool(os.getenv("DATABASE_URL"))
if USING_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
# ==================================


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
        # Neon yêu cầu sslmode=require (đã có trong DATABASE_URL)
        return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
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
seed_if_empty()
# ==================================================


# ==== Routes ====
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
    # Lấy dữ liệu BXH
    if USING_POSTGRES:
        with get_db() as con, con.cursor() as cur:
            cur.execute("""
                SELECT rank, player, trainer, rounds, kos, team, team_img
                FROM leaderboard
                ORDER BY rank ASC, player ASC
            """)
            rows = cur.fetchall()
            leaderboard = [dict(r) for r in rows]
    else:
        with get_db() as con:
            rows = con.execute("""
                SELECT rank, player, trainer, rounds, kos, team, team_img
                FROM leaderboard
                ORDER BY rank ASC, player ASC
            """).fetchall()
            leaderboard = [dict(r) for r in rows]

    # Fallback ảnh: chỉ gắn URL nếu file còn trên đĩa
    for d in leaderboard:
        d["team_thumb_url"] = None
        d["team_img_url"] = None
        fn = d.get("team_img")
        if fn:
            full_path = os.path.join(app.config['UPLOAD_FOLDER'], fn)
            if os.path.isfile(full_path):
                d["team_img_url"] = url_for('static', filename='uploads/' + fn)
                d["team_thumb_url"] = url_for('static', filename='uploads/thumbs/' + fn)

    return render_template("board_babel.html", leaderboard=leaderboard)


@app.post("/upload")
def upload_team_image():
    # giữ ngôn ngữ hiện tại sau khi upload
    lang = request.args.get('lang')

    player = (request.form.get('player') or '').strip() or 'Unknown'
    file = request.files.get('image')

    if not file or file.filename == '' or not allowed_file(file.filename):
        return redirect(url_for('board', lang=lang))

    ext = file.filename.rsplit('.', 1)[1].lower()
    safe = secure_filename(os.path.splitext(file.filename)[0])[:32]
    fname = f"{safe}-{uuid.uuid4().hex[:8]}.{ext}"

    # Lưu ảnh + thumbnail (ephemeral trên PaaS)
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
    file.save(save_path)
    try:
        thumb_path = os.path.join(app.config['THUMB_FOLDER'], fname)
        make_thumb(save_path, thumb_path, size=(48, 48))
    except Exception:
        pass

    # Cập nhật DB: nếu có player thì update ảnh, không có thì chèn mới với rank kế tiếp
    if USING_POSTGRES:
        with get_db() as con, con.cursor() as cur:
            # tìm theo player không phân biệt hoa/thường
            cur.execute("SELECT id FROM leaderboard WHERE player ILIKE %s", (player,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE leaderboard SET team_img=%s, updated_at=%s WHERE id=%s",
                    (fname, datetime.utcnow().isoformat(), row["id"])
                )
            else:
                cur.execute("SELECT COALESCE(MAX(rank),0)+1 AS n FROM leaderboard")
                next_rank = cur.fetchone()["n"]
                cur.execute(
                    "INSERT INTO leaderboard (rank, player, team_img, updated_at) VALUES (%s,%s,%s,%s)",
                    (next_rank, player, fname, datetime.utcnow().isoformat())
                )
            con.commit()
    else:
        with get_db() as con:
            row = con.execute("SELECT id FROM leaderboard WHERE player = ? COLLATE NOCASE", (player,)).fetchone()
            if row:
                con.execute(
                    "UPDATE leaderboard SET team_img=?, updated_at=? WHERE id=?",
                    (fname, datetime.utcnow().isoformat(), row["id"])
                )
            else:
                next_rank = con.execute("SELECT COALESCE(MAX(rank),0)+1 AS n FROM leaderboard").fetchone()["n"]
                con.execute(
                    "INSERT INTO leaderboard (rank, player, team_img, updated_at) VALUES (?,?,?,?)",
                    (next_rank, player, fname, datetime.utcnow().isoformat())
                )

    return redirect(url_for('board', lang=lang))
# ==================


if __name__ == "__main__":
    # Local dev
    app.run(debug=True)
