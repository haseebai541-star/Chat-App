"""
Private Chat App (Phase 3)
----------------------------
Everything from Phase 2, plus:
  - Group chats (create a group, add people by their Chat ID)
  - In group chats, each sender's name AND Chat ID is shown
  - "Seen by ..." on group messages (Instagram-style)
  - Stories (24-hour disappearing photo/video updates)
  - Admin panel (Haseeb Ashraf / haseebai541@gmail.com only):
        - see every registered user and their Chat ID
        - see every user's chats/groups (read-only)
        - remove any user from the platform entirely
  - "Forgot password" with an email verification link
  - Sending a photo always asks: "View Once" or "Normal" - sender's choice
  - Basic screenshot/copy deterrents (right-click, drag, double-click
    disabled on media) - NOTE: no website can fully block screenshots,
    this only discourages casual copying, it is not a guarantee.
  - Refreshed, more polished visual theme

Run:
    pip install -r requirements.txt
    python app.py

Email (forgot password) needs these environment variables set on your
hosting platform (Railway > Variables tab):
    SMTP_EMAIL     - a Gmail address you control
    SMTP_APP_PASSWORD - a Gmail "App Password" (not your normal password)
If these are not set, "forgot password" will show a friendly error
instead of crashing.
"""

import os
import sqlite3
import uuid
import string
import random
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", BASE_DIR)
DB_PATH = os.path.join(DATA_DIR, "chat.db")
PERSISTENT_UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
PROFILE_DIR = os.path.join(PERSISTENT_UPLOAD_DIR, "profiles")
MEDIA_DIR = os.path.join(PERSISTENT_UPLOAD_DIR, "media")
STORY_DIR = os.path.join(PERSISTENT_UPLOAD_DIR, "stories")
os.makedirs(PROFILE_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(STORY_DIR, exist_ok=True)

ADMIN_EMAIL = "haseebai541@gmail.com"
ADMIN_NAME = "Haseeb Ashraf"

SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")
SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD", "")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'please-change-this-secret-key'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=50 * 1024 * 1024)

ONLINE_USERS = {}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL, profile_pic TEXT DEFAULT '',
        is_admin INTEGER DEFAULT 0, created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS contacts (
        user_id TEXT NOT NULL, contact_id TEXT NOT NULL,
        PRIMARY KEY (user_id, contact_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS groups (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, creator_id TEXT, created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS group_members (
        group_id TEXT NOT NULL, user_id TEXT NOT NULL,
        PRIMARY KEY (group_id, user_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL,
        sender_id TEXT NOT NULL, receiver_id TEXT, group_id TEXT,
        type TEXT NOT NULL, content TEXT, filename TEXT DEFAULT '',
        view_once INTEGER DEFAULT 0, viewed INTEGER DEFAULT 0,
        seen INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0, timestamp TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS message_seen (
        message_id INTEGER NOT NULL, user_id TEXT NOT NULL,
        PRIMARY KEY (message_id, user_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS password_resets (
        token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stories (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, media_url TEXT,
        type TEXT, created_at TEXT, expires_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS story_views (
        story_id TEXT NOT NULL, viewer_id TEXT NOT NULL,
        PRIMARY KEY (story_id, viewer_id)
    )""")
    conn.commit()
    conn.close()


def generate_unique_id(table="users"):
    chars = string.ascii_uppercase + string.digits
    conn = get_db()
    while True:
        new_id = ''.join(random.choices(chars, k=8))
        exists = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (new_id,)).fetchone()
        if not exists:
            conn.close()
            return new_id


def room_name(id1, id2):
    return "dm_" + "_".join(sorted([id1, id2]))


def send_email(to_email, subject, body):
    if not SMTP_EMAIL or not SMTP_APP_PASSWORD:
        return False, "Email sending is not configured on the server yet."
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = SMTP_EMAIL
        msg['To'] = to_email
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
            server.send_message(msg)
        return True, ""
    except Exception as e:
        return False, str(e)


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
        user = current_user()
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


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or not password:
            return render_template("signup.html", error="Please fill in all fields.")

        conn = get_db()
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            conn.close()
            return render_template("signup.html", error="An account with this email already exists.")

        user_id = generate_unique_id("users")
        is_admin = 1 if email == ADMIN_EMAIL else 0
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, email, generate_password_hash(password), is_admin, datetime.utcnow().isoformat())
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


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user:
            token = uuid.uuid4().hex
            expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
            conn.execute("INSERT INTO password_resets (token, user_id, expires_at) VALUES (?, ?, ?)",
                         (token, user['id'], expires))
            conn.commit()
            reset_link = url_for('reset_password', token=token, _external=True)
            ok, err = send_email(
                email, "Reset your Chat App password",
                f"Hello {user['name']},\n\nClick this link to reset your password (valid for 1 hour):\n{reset_link}\n\nIf you didn't request this, ignore this email."
            )
            conn.close()
            if not ok:
                return render_template("forgot_password.html", error=f"Could not send email: {err}")
        else:
            conn.close()

        return render_template("forgot_password.html", success="If that email exists, a reset link has been sent.")

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_db()
    reset = conn.execute("SELECT * FROM password_resets WHERE token = ?", (token,)).fetchone()

    if not reset or datetime.fromisoformat(reset['expires_at']) < datetime.utcnow():
        conn.close()
        return render_template("reset_password.html", error="This reset link is invalid or has expired.", invalid=True)

    if request.method == "POST":
        new_password = request.form.get("password", "")
        if len(new_password) < 6:
            conn.close()
            return render_template("reset_password.html", error="Password must be at least 6 characters.")
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (generate_password_hash(new_password), reset['user_id']))
        conn.execute("DELETE FROM password_resets WHERE token = ?", (token,))
        conn.commit()
        conn.close()
        return render_template("login.html", error=None, success="Password updated. Please log in.")

    conn.close()
    return render_template("reset_password.html")


@app.route("/")
@login_required
def dashboard():
    user = current_user()
    conn = get_db()
    contacts = conn.execute("""
        SELECT u.id, u.name, u.profile_pic FROM contacts c JOIN users u ON u.id = c.contact_id
        WHERE c.user_id = ? ORDER BY u.name
    """, (user['id'],)).fetchall()
    my_groups = conn.execute("""
        SELECT g.id, g.name FROM group_members gm JOIN groups g ON g.id = gm.group_id
        WHERE gm.user_id = ? ORDER BY g.name
    """, (user['id'],)).fetchall()

    now = datetime.utcnow().isoformat()
    contact_ids = [c['id'] for c in contacts] + [user['id']]
    stories = []
    if contact_ids:
        placeholders = ",".join(["?"] * len(contact_ids))
        rows = conn.execute(f"""
            SELECT s.*, u.name, u.profile_pic FROM stories s JOIN users u ON u.id = s.user_id
            WHERE s.user_id IN ({placeholders}) AND s.expires_at > ?
            ORDER BY s.created_at DESC
        """, (*contact_ids, now)).fetchall()
        seen_story_ids = {r['story_id'] for r in conn.execute(
            "SELECT story_id FROM story_views WHERE viewer_id = ?", (user['id'],)
        ).fetchall()}
        by_user = {}
        for r in rows:
            by_user.setdefault(r['user_id'], {"name": r['name'], "profile_pic": r['profile_pic'],
                                               "user_id": r['user_id'], "stories": [], "all_seen": True})
            by_user[r['user_id']]["stories"].append(dict(r))
            if r['id'] not in seen_story_ids:
                by_user[r['user_id']]["all_seen"] = False
        stories = list(by_user.values())

    conn.close()
    online_ids = set(ONLINE_USERS.keys())
    return render_template("dashboard.html", user=user, contacts=contacts, my_groups=my_groups,
                            online_ids=online_ids, stories=stories)


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
    messages = conn.execute("SELECT * FROM messages WHERE room = ? AND deleted = 0 ORDER BY id ASC", (room,)).fetchall()
    conn.execute("UPDATE messages SET seen = 1 WHERE room = ? AND receiver_id = ? AND seen = 0", (room, user['id']))
    conn.commit()
    conn.close()

    is_online = other['id'] in ONLINE_USERS
    return render_template("chat.html", user=user, other=other, room=room, messages=messages, is_online=is_online)


@app.route("/create-group", methods=["GET", "POST"])
@login_required
def create_group():
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        ids_raw = request.form.get("member_ids", "").strip().upper()
        member_ids = [x.strip() for x in ids_raw.replace("\n", ",").split(",") if x.strip()]

        if not name:
            return render_template("create_group.html", error="Please enter a group name.")

        conn = get_db()
        valid_members = []
        for mid in member_ids:
            row = conn.execute("SELECT id FROM users WHERE id = ?", (mid,)).fetchone()
            if row:
                valid_members.append(row['id'])

        group_id = generate_unique_id("groups")
        conn.execute("INSERT INTO groups (id, name, creator_id, created_at) VALUES (?, ?, ?, ?)",
                     (group_id, name, user['id'], datetime.utcnow().isoformat()))
        conn.execute("INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?, ?)", (group_id, user['id']))
        for mid in valid_members:
            conn.execute("INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?, ?)", (group_id, mid))
        conn.commit()
        conn.close()
        return redirect(url_for('group_chat', group_id=group_id))

    return render_template("create_group.html")


@app.route("/group/<group_id>")
@login_required
def group_chat(group_id):
    user = current_user()
    conn = get_db()
    group = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    is_member = conn.execute("SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                              (group_id, user['id'])).fetchone()
    if not group or not is_member:
        conn.close()
        return "Group not found or you are not a member.", 404

    members = conn.execute("""
        SELECT u.id, u.name, u.profile_pic FROM group_members gm JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id = ?
    """, (group_id,)).fetchall()

    room = "group_" + group_id
    messages = conn.execute("SELECT * FROM messages WHERE room = ? AND deleted = 0 ORDER BY id ASC", (room,)).fetchall()

    for m in messages:
        conn.execute("INSERT OR IGNORE INTO message_seen (message_id, user_id) VALUES (?, ?)", (m['id'], user['id']))
    conn.commit()

    member_map = {m['id']: m['name'] for m in members}
    conn.close()

    return render_template("group_chat.html", user=user, group=group, room=room,
                            messages=messages, members=members, member_map=member_map,
                            online_ids=set(ONLINE_USERS.keys()))


@app.route("/story/upload", methods=["POST"])
@login_required
def upload_story():
    user = current_user()
    file = request.files.get('file')
    if not file:
        return redirect(url_for('dashboard'))

    ext = os.path.splitext(secure_filename(file.filename))[1]
    fname = f"{uuid.uuid4().hex}{ext}"
    file.save(os.path.join(STORY_DIR, fname))

    story_id = generate_unique_id("stories")
    story_type = "video" if file.mimetype.startswith("video") else "image"
    now = datetime.utcnow()
    conn = get_db()
    conn.execute("INSERT INTO stories (id, user_id, media_url, type, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (story_id, user['id'], f"/media/stories/{fname}", story_type,
                  now.isoformat(), (now + timedelta(hours=24)).isoformat()))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))


@app.route("/story/view/<user_id>")
@login_required
def view_stories(user_id):
    viewer = current_user()
    conn = get_db()
    now = datetime.utcnow().isoformat()
    owner = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    stories = conn.execute("SELECT * FROM stories WHERE user_id = ? AND expires_at > ? ORDER BY created_at ASC",
                            (user_id, now)).fetchall()
    for s in stories:
        conn.execute("INSERT OR IGNORE INTO story_views (story_id, viewer_id) VALUES (?, ?)", (s['id'], viewer['id']))
    conn.commit()
    conn.close()
    return render_template("view_story.html", owner=owner, stories=stories)


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


@app.route("/admin")
@login_required
@admin_required
def admin_panel():
    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    total_messages = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()['c']
    conn.close()
    return render_template("admin.html", users=users, total_messages=total_messages,
                            admin_name=ADMIN_NAME, admin_email=ADMIN_EMAIL)


@app.route("/admin/user/<user_id>")
@login_required
@admin_required
def admin_view_user(user_id):
    conn = get_db()
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    contacts = conn.execute("""
        SELECT u.id, u.name FROM contacts c JOIN users u ON u.id = c.contact_id WHERE c.user_id = ?
    """, (user_id,)).fetchall()
    groups = conn.execute("""
        SELECT g.id, g.name FROM group_members gm JOIN groups g ON g.id = gm.group_id WHERE gm.user_id = ?
    """, (user_id,)).fetchall()
    conn.close()
    return render_template("admin_user_detail.html", target=target, contacts=contacts, groups=groups)


@app.route("/admin/view-room/<path:room>")
@login_required
@admin_required
def admin_view_room(room):
    conn = get_db()
    messages = conn.execute("SELECT * FROM messages WHERE room = ? AND deleted = 0 ORDER BY id ASC", (room,)).fetchall()
    sender_ids = {m['sender_id'] for m in messages}
    names = {}
    for sid in sender_ids:
        u = conn.execute("SELECT name FROM users WHERE id = ?", (sid,)).fetchone()
        names[sid] = u['name'] if u else "Unknown"
    conn.close()
    return render_template("admin_room_view.html", messages=messages, names=names, room=room)


@app.route("/admin/remove-user/<user_id>", methods=["POST"])
@login_required
@admin_required
def admin_remove_user(user_id):
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.execute("DELETE FROM contacts WHERE user_id = ? OR contact_id = ?", (user_id, user_id))
    conn.execute("DELETE FROM group_members WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM messages WHERE sender_id = ? OR receiver_id = ?", (user_id, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))


@app.route("/media/profiles/<filename>")
def serve_profile_pic(filename):
    return send_from_directory(PROFILE_DIR, filename)


@app.route("/media/chat/<filename>")
def serve_chat_media(filename):
    return send_from_directory(MEDIA_DIR, filename)


@app.route("/media/stories/<filename>")
def serve_story_media(filename):
    return send_from_directory(STORY_DIR, filename)


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
    receiver_id = data.get('receiver_id')
    group_id = data.get('group_id')
    msg_type = data.get('type', 'text')
    content = data.get('content')
    filename = data.get('filename', '')
    view_once = 1 if data.get('view_once') else 0
    timestamp = datetime.utcnow().isoformat()

    conn = get_db()
    cur = conn.execute("""
        INSERT INTO messages (room, sender_id, receiver_id, group_id, type, content, filename, view_once, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (room, user_id, receiver_id, group_id, msg_type, content, filename, view_once, timestamp))
    msg_id = cur.lastrowid
    conn.execute("INSERT OR IGNORE INTO message_seen (message_id, user_id) VALUES (?, ?)", (msg_id, user_id))

    sender = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.commit()
    conn.close()

    payload = {
        "id": msg_id, "sender_id": user_id, "sender_name": sender['name'] if sender else "",
        "receiver_id": receiver_id, "group_id": group_id, "type": msg_type, "content": content,
        "filename": filename, "view_once": view_once, "viewed": 0, "seen": 0, "timestamp": timestamp
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


@socketio.on('mark_seen_group')
def on_mark_seen_group(data):
    room = data['room']
    viewer_id = session.get('user_id')
    conn = get_db()
    unseen = conn.execute("""
        SELECT m.id FROM messages m
        WHERE m.room = ? AND m.sender_id != ? AND m.deleted = 0
        AND NOT EXISTS (SELECT 1 FROM message_seen s WHERE s.message_id = m.id AND s.user_id = ?)
    """, (room, viewer_id, viewer_id)).fetchall()

    for row in unseen:
        conn.execute("INSERT OR IGNORE INTO message_seen (message_id, user_id) VALUES (?, ?)", (row['id'], viewer_id))
    conn.commit()

    viewer = conn.execute("SELECT name FROM users WHERE id = ?", (viewer_id,)).fetchone()
    conn.close()

    for row in unseen:
        emit('group_message_seen', {"message_id": row['id'], "viewer_name": viewer['name']}, room=room)


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
