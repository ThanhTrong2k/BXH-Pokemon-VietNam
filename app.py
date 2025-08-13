# ==== imports ====
import os
import uuid
import sqlite3
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, make_response
from flask_babel import Babel, gettext as _, get_locale
from werkzeug.utils import secure_filename
from PIL import Image
# ==== end imports ====

app = Flask(__name__)

# ---- Babel (API mới) ----
app.config['BABEL_DEFAULT_LOCALE'] = 'vi'
app.config['BABEL_DEFAULT_TIMEZONE'] = 'Asia/Ho_Chi_Minh'
app.config['BABEL_SUPPORTED_LOCALES'] = ['vi', 'en']
app.config['TEMPLATES_AUTO_RELOAD'] = True

babel = Babel()

def select_locale():
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

# expose get_locale cho Jinja
app.jinja_env.globals['get_locale'] = lambda: str(get_locale())

# ---- Upload config ----
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

# ---- SQLite ----
# cũ:
# DB_PATH = os.path.join(app.root_path, 'leaderboard.db')

# mới:
DB_PATH = os.getenv('DB_PATH', os.path.join(app.root_path, 'leaderboard.db'))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
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
                team_img TEXT DEFAULT NULL,
                updated_at TEXT
            )
        """)

def seed_if_empty():
    with get_db() as con:
        c = con.execute("SELECT COUNT(*) AS c FROM leaderboard").fetchone()["c"]
        if c == 0:
            con.executemany(
                "INSERT INTO leaderboard (rank, player, trainer, rounds, kos, team, team_img, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (1, "Trong", 8, 6, 1, "Charizard, Gardevoir, Swampert", None, datetime.utcnow().isoformat()),
                    (2, "Minh",  7, 5, 0, "Blaziken, Metagross, Milotic",  None, datetime.utcnow().isoformat()),
                    (3, "Lan",   6, 4, 0, "Lucario, Tyranitar, Jolteon",  None, datetime.utcnow().isoformat()),
                ]
            )

init_db()
seed_if_empty()

# ---- Routes ----
@app.route("/")
def home():
    return redirect(url_for('board'))

@app.route("/set-language/<lang_code>")
def set_language(lang_code):
    if lang_code not in app.config['BABEL_SUPPORTED_LOCALES']:
        lang_code = app.config['BABEL_DEFAULT_LOCALE']
    resp = make_response(redirect(request.referrer or url_for('board')))
    resp.set_cookie("lang", lang_code, max_age=60*60*24*365)
    return resp

@app.route("/board")
def board():
    with get_db() as con:
        rows = con.execute("""
            SELECT rank, player, trainer, rounds, kos, team, team_img
            FROM leaderboard
            ORDER BY rank ASC, player ASC
        """).fetchall()
    leaderboard = []
    for r in rows:
        d = dict(r)
        d["team_thumb_url"] = None
        d["team_img_url"] = None
        fn = d.get("team_img")
        if fn:
            full_path = os.path.join(app.config['UPLOAD_FOLDER'], fn)
            if os.path.isfile(full_path):
                d["team_img_url"] = url_for('static', filename='uploads/' + fn)
                d["team_thumb_url"] = url_for('static', filename='uploads/thumbs/' + fn)
            # nếu không có file → giữ None để template chỉ hiện tên
        leaderboard.append(d)

    return render_template("board_babel.html", leaderboard=leaderboard)

@app.post("/upload")
def upload_team_image():
    # giữ ?lang=... sau khi upload
    lang = request.args.get('lang')

    player = (request.form.get('player') or '').strip() or 'Unknown'
    file = request.files.get('image')

    if not file or file.filename == '':
        return redirect(url_for('board', lang=lang))

    if not allowed_file(file.filename):
        return redirect(url_for('board', lang=lang))

    ext = file.filename.rsplit('.', 1)[1].lower()
    safe = secure_filename(os.path.splitext(file.filename)[0])[:32]
    fname = f"{safe}-{uuid.uuid4().hex[:8]}.{ext}"

    save_path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
    file.save(save_path)

    # tạo thumbnail
    try:
        thumb_path = os.path.join(app.config['THUMB_FOLDER'], fname)
        make_thumb(save_path, thumb_path, size=(48, 48))
    except Exception:
        pass

    # cập nhật DB
    with get_db() as con:
        row = con.execute(
            "SELECT id FROM leaderboard WHERE player = ? COLLATE NOCASE",
            (player,)
        ).fetchone()

        if row is not None:
            con.execute(
                "UPDATE leaderboard SET team_img = ?, updated_at = ? WHERE id = ?",
                (fname, datetime.utcnow().isoformat(), row["id"])
            )
        else:
            next_rank = con.execute(
                "SELECT COALESCE(MAX(rank), 0) + 1 AS n FROM leaderboard"
            ).fetchone()["n"]
            con.execute(
                "INSERT INTO leaderboard (rank, player, team_img, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (next_rank, player, fname, datetime.utcnow().isoformat())
            )

    return redirect(url_for('board', lang=lang))

if __name__ == "__main__":
    app.run(debug=True)
