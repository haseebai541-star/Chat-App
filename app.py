"""
Private Chat App (Phase 2)
----------------------------
New features in this version:
  - User accounts (signup/login) with a unique Chat ID for every user
  - Add someone only by typing their exact Chat ID (no public user list,
    nobody can browse who else has an account)
  - Strictly 1-to-1 private chats (no group chats)
  - Online / offline status
  - Typing indicator ("... is typing")
  - Seen / delivered ticks (like WhatsApp)
  - Manual, permanent message delete (stored in the database, so it
    will NOT come back after a refresh)
  - View-once photos (like WhatsApp's "view once")
  - Editable profile: display name + profile picture
  - One hardcoded Admin account (Haseeb Ashraf) - only that email can
    ever get admin/settings rights, no matter who signs up
  - Mobile-responsive UI

Run:
    pip install -r requirements.txt
    python app.py
"""

import os
import sqlite3
import uuid
import string
import random
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Agar Railway par ek "Volume" attach ki gayi ho, to RAILWAY_VOLUME_MOUNT_PATH
# environment variable khud-ba-khud mil jati hai - hum wahan data store
# karenge taake naye deployment par bhi data (users, messages, photos)
# permanent rahe, delete na ho.
DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", BASE_DIR)
DB_PATH = os.path.join(DATA_DIR, "chat.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
PERSISTENT_UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
PROFILE_DIR = os.path.join(PERSISTENT_UPLOAD_DIR, "profiles")
MEDIA_DIR = os.path.join(PERSISTENT_UPLOAD_DIR, "media")
os.makedirs(PROFILE_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR, exist_ok=True)

# ---- Only this exact email can ever be an admin, on any account ----
ADMIN_EMAIL = "haseebai541@gmail.com"
ADMIN_NAME = "Haseeb Ashraf"

app = Flask(__name__)
app.config['SECRET_KEY'] = 'please-change-this-secret-key'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB uploads

socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=50 * 1024 * 1024)

# user_id -> set of active socket ids (a user can have multiple tabs/devices)
ONLINE_USERS = {}


# ---------------------------------------------------------------- DB setup
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            profile_pic TEXT DEFAULT '',
            is_admin INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            user_id TEXT NOT NULL,
            contact_id TEXT NOT NULL,
            PRIMARY KEY (user_id, contact_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            receiver_id TEXT NOT NULL,
            type TEXT NOT NULL,
            content TEXT,
            filename TEXT DEFAULT '',
            view_once INTEGER DEFAULT 0,
            viewed INTEGER DEFAULT 0,
            seen INTEGER DEFAULT 0,
            deleted INTEGER DEFAULT 0,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()


def generate_unique_id():
    """8-character Chat ID, e.g. 7K2N9QAB"""
    chars = string.ascii_uppercase + string.digits
    conn = get_db()
    while True:
        new_id = ''.join(random.choices(chars, k=8))
        exists = conn.execute("SELECT 1 FROM users WHERE id = ?", (new_id,)).fetchone()
        if not exists:
            conn.close()
            return new_id


def room_name(id1, id2):
    return "_".join(sorted([id1, id2]))


# ---------------------------------------------------------------- Auth helpers
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
        conn.close()
        if not user or not user['is_admin']:
            return "Access denied: admin only.", 403
        return f(*args, **kwargs)
    return wrapper


def current_user():
    if 'user_id' not in session:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    conn.close()
    return user


# ---------------------------------------------------------------- Auth routes
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            return render_template("signup.html", error="Please fill in all fields.")

        conn = get_db()
        existing = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            return render_template("signup.html", error="An account with this email already exists.")

        user_id = generate_unique_id()
        is_admin = 1 if email == ADMIN_EMAIL else 0
        password_hash = generate_password_hash(password)

        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, email, password_hash, is_admin, datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()

        session['user_id'] = user_id
        return redirect(url_for('dashboard'))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        if not user or not check_password_hash(user['password_hash'], password):
            return render_template("login.html", error="Incorrect email or password.")

        session['user_id'] = user['id']
        return redirect(url_for('dashboard'))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------- Main pages
@app.route("/")
@login_required
def dashboard():
    user = current_user()
    conn = get_db()
    contacts = conn.execute("""
        SELECT u.id, u.name, u.profile_pic
        FROM contacts c JOIN users u ON u.id = c.contact_id
        WHERE c.user_id = ?
        ORDER BY u.name
    """, (user['id'],)).fetchall()
    conn.close()

    online_ids = set(ONLINE_USERS.keys())
    return render_template("dashboard.html", user=user, contacts=contacts, online_ids=online_ids)


@app.route("/find-user", methods=["POST"])
@login_required
def find_user():
    user = current_user()
    target_id = request.form.get("chat_id", "").strip().upper()

    if target_id == user['id']:
        return render_template("dashboard_result.html", error="You can't add yourself.")

    conn = get_db()
    target = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()

    if not target:
        conn.close()
        return render_template("dashboard_result.html", error="No user found with that Chat ID.")

    # Add each other as contacts if not already
    conn.execute("INSERT OR IGNORE INTO contacts (user_id, contact_id) VALUES (?, ?)", (user['id'], target['id']))
    conn.execute("INSERT OR IGNORE INTO contacts (user_id, contact_id) VALUES (?, ?)", (target['id'], user['id']))
    conn.commit()
    conn.close()

    return redirect(url_for('chat_room', other_id=target['id']))


@app.route("/chat/<other_id>")
@login_required
def chat_room(other_id):
    user = current_user()
    conn = get_db()
    other = conn.execute("SELECT * FROM users WHERE id = ?", (other_id,)).fetchone()
    if not other:
        conn.close()
        return "User not found.", 404

    room = room_name(user['id'], other['id'])
    messages = conn.execute("""
        SELECT * FROM messages WHERE room = ? AND deleted = 0 ORDER BY id ASC
    """, (room,)).fetchall()

    # Mark messages sent TO me as seen
    conn.execute("UPDATE messages SET seen = 1 WHERE room = ? AND receiver_id = ? AND seen = 0", (room, user['id']))
    conn.commit()
    conn.close()

    is_online = other['id'] in ONLINE_USERS

    return render_template(
        "chat.html", user=user, other=other, room=room,
        messages=messages, is_online=is_online
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        new_name = request.form.get("name", "").strip()
        conn = get_db()
        if new_name:
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (new_name, user['id']))

        file = request.files.get("profile_pic")
        if file and file.filename:
            ext = os.path.splitext(secure_filename(file.filename))[1]
            fname = f"{user['id']}_{uuid.uuid4().hex}{ext}"
            file.save(os.path.join(PROFILE_DIR, fname))
            conn.execute("UPDATE users SET profile_pic = ? WHERE id = ?", (fname, user['id']))

        conn.commit()
        conn.close()
        return redirect(url_for('profile'))

    return render_template("profile.html", user=user)


# ---------------------------------------------------------------- Admin (Haseeb Ashraf only)
@app.route("/admin")
@admin_required
def admin_panel():
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()['c']
    total_messages = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()['c']
    conn.close()
    return render_template("admin.html", total_users=total_users, total_messages=total_messages,
                            admin_name=ADMIN_NAME, admin_email=ADMIN_EMAIL)


# ---------------------------------------------------------------- Serve persistent files
@app.route("/media/profiles/<filename>")
def serve_profile_pic(filename):
    return send_from_directory(PROFILE_DIR, filename)


@app.route("/media/chat/<filename>")
def serve_chat_media(filename):
    return send_from_directory(MEDIA_DIR, filename)


# ---------------------------------------------------------------- File upload (chat media)
@app.route("/upload/<room>", methods=["POST"])
@login_required
def upload_media(room):
    file = request.files.get('file')
    if not file:
        return {"error": "No file"}, 400
    ext = os.path.splitext(secure_filename(file.filename))[1]
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file.save(os.path.join(MEDIA_DIR, unique_name))
    return {"url": url_for('serve_chat_media', filename=unique_name), "filename": file.filename}


# ---------------------------------------------------------------- Socket.IO events
@socketio.on('connect')
def on_connect():
    user_id = session.get('user_id')
    if not user_id:
        return
    ONLINE_USERS.setdefault(user_id, set()).add(request.sid)
    emit('presence', {"user_id": user_id, "online": True}, broadcast=True)


@socketio.on('disconnect')
def on_disconnect():
    user_id = session.get('user_id')
    if not user_id or user_id not in ONLINE_USERS:
        return
    ONLINE_USERS[user_id].discard(request.sid)
    if not ONLINE_USERS[user_id]:
        del ONLINE_USERS[user_id]
        emit('presence', {"user_id": user_id, "online": False}, broadcast=True)


@socketio.on('join')
def on_join(data):
    join_room(data['room'])


@socketio.on('typing')
def on_typing(data):
    emit('typing', {"user_id": session.get('user_id')}, room=data['room'], include_self=False)


@socketio.on('stop_typing')
def on_stop_typing(data):
    emit('stop_typing', {"user_id": session.get('user_id')}, room=data['room'], include_self=False)


@socketio.on('send_message')
def on_send_message(data):
    user_id = session.get('user_id')
    if not user_id:
        return

    room = data['room']
    receiver_id = data['receiver_id']
    msg_type = data.get('type', 'text')
    content = data.get('content')
    filename = data.get('filename', '')
    view_once = 1 if data.get('view_once') else 0
    timestamp = datetime.utcnow().isoformat()

    conn = get_db()
    cur = conn.execute("""
        INSERT INTO messages (room, sender_id, receiver_id, type, content, filename, view_once, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (room, user_id, receiver_id, msg_type, content, filename, view_once, timestamp))
    msg_id = cur.lastrowid
    conn.commit()
    conn.close()

    payload = {
        "id": msg_id, "sender_id": user_id, "receiver_id": receiver_id,
        "type": msg_type, "content": content, "filename": filename,
        "view_once": view_once, "viewed": 0, "seen": 0, "timestamp": timestamp
    }
    emit('receive_message', payload, room=room)


@socketio.on('mark_seen')
def on_mark_seen(data):
    room = data['room']
    viewer_id = session.get('user_id')
    conn = get_db()
    conn.execute("UPDATE messages SET seen = 1 WHERE room = ? AND receiver_id = ? AND seen = 0", (room, viewer_id))
    conn.commit()
    conn.close()
    emit('messages_seen', {"by": viewer_id}, room=room, include_self=False)


@socketio.on('delete_message')
def on_delete_message(data):
    msg_id = data['message_id']
    room = data['room']
    user_id = session.get('user_id')

    conn = get_db()
    msg = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if msg and msg['sender_id'] == user_id:
        conn.execute("UPDATE messages SET deleted = 1 WHERE id = ?", (msg_id,))
        conn.commit()
    conn.close()

    emit('message_deleted', {"message_id": msg_id}, room=room)


@socketio.on('view_once_opened')
def on_view_once_opened(data):
    msg_id = data['message_id']
    room = data['room']
    conn = get_db()
    conn.execute("UPDATE messages SET viewed = 1 WHERE id = ?", (msg_id,))
    conn.commit()
    conn.close()
    emit('view_once_consumed', {"message_id": msg_id}, room=room)


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, debug=False, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
