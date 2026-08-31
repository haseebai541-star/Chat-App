"""
Private Chat App (Phase 3)
----------------------------
New in this version (on top of Phase 2):
  - Group chats (create a group from your existing contacts)
  - "Seen by" tracking in group chats (like Instagram group seen)
  - Sending a photo now shows an explicit choice every time:
    "View Once" vs "Normal" (instead of a single confirm() popup)
  - Forgot password -> a 6-digit code is emailed to the account's
    email address -> user enters the code -> sets a new password.
    (Configure SMTP_* environment variables to actually send email;
    see README section at the bottom of this file.)
  - Admin panel: see every user (name, Chat ID, email, join date,
    message count), remove any user's account, ban/unban a user,
    and toggle whether normal users are allowed to turn on
    24-hour auto-delete for their chats.
  - The admin's own Chat ID is never shown to, or discoverable by,
    any other user (find-user cannot resolve it either).
  - Stories (24-hour expiring photo/video updates, like Instagram),
    with a "seen by" list visible to the story's owner.
  - Sessions are permanent: once logged in, you stay logged in
    until you tap "Log Out" (no auto-expiry).
  - Optional 24-hour auto-delete per 1-to-1 chat / group (each
    conversation's participants can turn it on, IF the admin has
    allowed the feature).
  - Best-effort "possible screenshot" notice (see note below --
    this can NOT be guaranteed or fully prevented on the web,
    unlike native mobile apps).
  - New professional/dark, "stylish" UI theme.

Run:
    pip install -r requirements.txt
    python app.py
"""

import os
import sqlite3
import uuid
import string
import random
import smtplib
import threading
import time
from email.mime.text import MIMEText
from datetime import datetime, timedelta
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
STORY_DIR = os.path.join(PERSISTENT_UPLOAD_DIR, "stories")
os.makedirs(PROFILE_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(STORY_DIR, exist_ok=True)

# ---- Only this exact email can ever be an admin, on any account ----
ADMIN_EMAIL = "haseebai541@gmail.com"
ADMIN_NAME = "Haseeb Ashraf"

# ---- Optional SMTP settings for real password-reset emails ----
# Set these as environment variables on your host (Railway/etc).
# Example for Gmail: create an "App Password" (not your normal
# password) at https://myaccount.google.com/apppasswords
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "please-change-this-secret-key")
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB uploads
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)  # stay logged in until manual logout

socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=50 * 1024 * 1024)

# user_id -> set of active socket ids (a user can have multiple tabs/devices)
ONLINE_USERS = {}


# ---------------------------------------------------------------- DB setup
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
            is_banned INTEGER DEFAULT 0,
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
        CREATE TABLE IF NOT EXISTS rooms (
            room TEXT PRIMARY KEY,
            auto_delete_24h INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_by TEXT NOT NULL,
            group_pic TEXT DEFAULT '',
            auto_delete_24h INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            PRIMARY KEY (group_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT DEFAULT '',
            group_id TEXT DEFAULT '',
            sender_id TEXT NOT NULL,
            receiver_id TEXT DEFAULT '',
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_seen_by (
            message_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            seen_at TEXT,
            PRIMARY KEY (message_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            media_url TEXT NOT NULL,
            media_type TEXT NOT NULL,
            caption TEXT DEFAULT '',
            created_at TEXT,
            expires_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS story_views (
            story_id INTEGER NOT NULL,
            viewer_id TEXT NOT NULL,
            viewed_at TEXT,
            PRIMARY KEY (story_id, viewer_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            email TEXT PRIMARY KEY,
            otp TEXT,
            expires_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('auto_delete_allowed', '1')")
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


def generate_group_id():
    conn = get_db()
    while True:
        new_id = "G" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))
        exists = conn.execute("SELECT 1 FROM groups WHERE id = ?", (new_id,)).fetchone()
        if not exists:
            conn.close()
            return new_id


def room_name(id1, id2):
    return "_".join(sorted([id1, id2]))


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()
    conn.close()


def auto_delete_allowed():
    return get_setting('auto_delete_allowed', '1') == '1'


# ---------------------------------------------------------------- Email helper
def send_email(to_email, subject, body):
    """
    Sends a real email if SMTP_* env vars are configured.
    If they are NOT configured, we can't magically deliver an email --
    so we log the code to the server console instead (dev fallback)
    and the /forgot-password page will say so.
    Returns True if it believes the email was actually sent.
    """
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        print(f"[DEV MODE - no SMTP configured] Would email {to_email}: {subject}\n{body}")
        return False
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = SMTP_FROM
        msg['To'] = to_email
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] Could not send to {to_email}: {e}")
        return False


# ---------------------------------------------------------------- Auth helpers
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
        conn.close()
        if not user or user['is_banned']:
            session.clear()
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


def is_admin_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return bool(row and row['is_admin'])


# ---------------------------------------------------------------- Auth routes
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            return render_template("signup.html", error="Please fill in all fields.")
        if "@" not in email or "." not in email:
            return render_template("signup.html", error="Please enter a valid email address.")
        if len(password) < 6:
            return render_template("signup.html", error="Password must be at least 6 characters.")

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

        session.permanent = True
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
        if user['is_banned']:
            return render_template("login.html", error="This account has been removed by the admin.")

        session.permanent = True
        session['user_id'] = user['id']
        return redirect(url_for('dashboard'))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------- Forgot / reset password
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        # Always show the same message whether or not the account exists,
        # so people can't use this form to find out who has an account.
        generic_msg = "If an account with that email exists, a 6-digit verification code has been sent to it."

        if user:
            otp = ''.join(random.choices(string.digits, k=6))
            expires = (datetime.utcnow() + timedelta(minutes=15)).isoformat()
            conn.execute("INSERT INTO password_resets (email, otp, expires_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(email) DO UPDATE SET otp = excluded.otp, expires_at = excluded.expires_at",
                         (email, otp, expires))
            conn.commit()
            sent = send_email(
                email,
                "Your password reset code",
                f"Your verification code is: {otp}\nThis code expires in 15 minutes.\n\n"
                f"If you didn't request this, you can ignore this email."
            )
            conn.close()
            # Dev fallback: if SMTP isn't configured, we can't actually deliver
            # the email, so surface the code on-screen with a clear warning
            # instead of silently failing.
            if not sent:
                return render_template("forgot_password.html",
                                        info=generic_msg,
                                        dev_otp=otp,
                                        dev_email=email)
            return render_template("forgot_password.html", info=generic_msg, sent_to=email)

        conn.close()
        return render_template("forgot_password.html", info=generic_msg)

    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        otp = request.form.get("otp", "").strip()
        new_password = request.form.get("new_password", "")

        if len(new_password) < 6:
            return render_template("reset_password.html", error="Password must be at least 6 characters.", email=email)

        conn = get_db()
        row = conn.execute("SELECT * FROM password_resets WHERE email = ?", (email,)).fetchone()

        if not row or row['otp'] != otp:
            conn.close()
            return render_template("reset_password.html", error="Invalid verification code.", email=email)

        if datetime.fromisoformat(row['expires_at']) < datetime.utcnow():
            conn.close()
            return render_template("reset_password.html", error="This code has expired. Please request a new one.", email=email)

        conn.execute("UPDATE users SET password_hash = ? WHERE email = ?",
                     (generate_password_hash(new_password), email))
        conn.execute("DELETE FROM password_resets WHERE email = ?", (email,))
        conn.commit()
        conn.close()
        return redirect(url_for('login'))

    email = request.args.get("email", "")
    return render_template("reset_password.html", email=email)


# ---------------------------------------------------------------- Main pages
@app.route("/")
@login_required
def dashboard():
    user = current_user()
    conn = get_db()
    contacts = conn.execute("""
        SELECT u.id, u.name, u.profile_pic
        FROM contacts c JOIN users u ON u.id = c.contact_id
        WHERE c.user_id = ? AND u.is_admin = 0
        ORDER BY u.name
    """, (user['id'],)).fetchall()

    my_groups = conn.execute("""
        SELECT g.* FROM groups g JOIN group_members gm ON gm.group_id = g.id
        WHERE gm.user_id = ? ORDER BY g.name
    """, (user['id'],)).fetchall()

    # Active stories from myself + my contacts
    now = datetime.utcnow().isoformat()
    contact_ids = [c['id'] for c in contacts] + [user['id']]
    stories_by_user = []
    if contact_ids:
        placeholders = ",".join("?" for _ in contact_ids)
        rows = conn.execute(f"""
            SELECT s.*, u.name as user_name, u.profile_pic as user_pic
            FROM stories s JOIN users u ON u.id = s.user_id
            WHERE s.user_id IN ({placeholders}) AND s.expires_at > ?
            ORDER BY s.created_at DESC
        """, (*contact_ids, now)).fetchall()
        seen_map = {}
        for r in rows:
            seen_map.setdefault(r['user_id'], []).append(r)
        stories_by_user = [{"user_id": uid, "name": items[0]['user_name'],
                             "pic": items[0]['user_pic'], "count": len(items)}
                            for uid, items in seen_map.items()]

    conn.close()

    online_ids = set(ONLINE_USERS.keys())
    return render_template("dashboard.html", user=user, contacts=contacts, groups=my_groups,
                            online_ids=online_ids, stories_by_user=stories_by_user)


@app.route("/find-user", methods=["POST"])
@login_required
def find_user():
    user = current_user()
    target_id = request.form.get("chat_id", "").strip().upper()

    if target_id == user['id']:
        return render_template("dashboard_result.html", error="You can't add yourself.")

    conn = get_db()
    target = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()

    # The admin account can never be found/added by ordinary users --
    # this keeps the admin's Chat ID completely hidden from everyone.
    if not target or (target['is_admin'] and not user['is_admin']):
        conn.close()
        return render_template("dashboard_result.html", error="No user found with that Chat ID.")

    if target['is_banned']:
        conn.close()
        return render_template("dashboard_result.html", error="This account is not available.")

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
    # Never let a non-admin land directly on the admin's chat either.
    if other['is_admin'] and not user['is_admin']:
        conn.close()
        return "User not found.", 404

    room = room_name(user['id'], other['id'])
    conn.execute("INSERT OR IGNORE INTO rooms (room, auto_delete_24h) VALUES (?, 0)", (room,))
    conn.commit()

    messages = conn.execute("""
        SELECT * FROM messages WHERE room = ? AND deleted = 0 ORDER BY id ASC
    """, (room,)).fetchall()

    conn.execute("UPDATE messages SET seen = 1 WHERE room = ? AND receiver_id = ? AND seen = 0", (room, user['id']))
    room_row = conn.execute("SELECT auto_delete_24h FROM rooms WHERE room = ?", (room,)).fetchone()
    conn.commit()
    conn.close()

    is_online = other['id'] in ONLINE_USERS

    return render_template(
        "chat.html", user=user, other=other, room=room, group=None,
        messages=messages, is_online=is_online,
        auto_delete_on=bool(room_row['auto_delete_24h']) if room_row else False,
        auto_delete_allowed=auto_delete_allowed()
    )


# ---------------------------------------------------------------- Groups
@app.route("/group/create", methods=["GET", "POST"])
@login_required
def create_group():
    user = current_user()
    conn = get_db()
    contacts = conn.execute("""
        SELECT u.id, u.name, u.profile_pic FROM contacts c JOIN users u ON u.id = c.contact_id
        WHERE c.user_id = ? AND u.is_admin = 0 ORDER BY u.name
    """, (user['id'],)).fetchall()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        member_ids = request.form.getlist("members")
        if not name:
            conn.close()
            return render_template("create_group.html", contacts=contacts, error="Please enter a group name.")
        if not member_ids:
            conn.close()
            return render_template("create_group.html", contacts=contacts, error="Pick at least one contact.")

        group_id = generate_group_id()
        conn.execute("INSERT INTO groups (id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                     (group_id, name, user['id'], datetime.utcnow().isoformat()))
        conn.execute("INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?, ?)", (group_id, user['id']))
        for mid in member_ids:
            # only allow adding people who are actually your contacts
            valid = conn.execute("SELECT 1 FROM contacts WHERE user_id = ? AND contact_id = ?",
                                  (user['id'], mid)).fetchone()
            if valid:
                conn.execute("INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?, ?)", (group_id, mid))
        conn.commit()
        conn.close()
        return redirect(url_for('group_chat', group_id=group_id))

    conn.close()
    return render_template("create_group.html", contacts=contacts)


@app.route("/group/<group_id>")
@login_required
def group_chat(group_id):
    user = current_user()
    conn = get_db()
    group = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    if not group:
        conn.close()
        return "Group not found.", 404
    membership = conn.execute("SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                               (group_id, user['id'])).fetchone()
    if not membership:
        conn.close()
        return "You're not a member of this group.", 403

    members = conn.execute("""
        SELECT u.id, u.name, u.profile_pic FROM group_members gm JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id = ? ORDER BY u.name
    """, (group_id,)).fetchall()

    messages = conn.execute("""
        SELECT * FROM messages WHERE group_id = ? AND deleted = 0 ORDER BY id ASC
    """, (group_id,)).fetchall()

    # mark seen-by for me on every message not sent by me
    now = datetime.utcnow().isoformat()
    for m in messages:
        if m['sender_id'] != user['id']:
            conn.execute("INSERT OR IGNORE INTO message_seen_by (message_id, user_id, seen_at) VALUES (?, ?, ?)",
                         (m['id'], user['id'], now))

    seen_rows = conn.execute("""
        SELECT message_id, user_id FROM message_seen_by
        WHERE message_id IN (SELECT id FROM messages WHERE group_id = ?)
    """, (group_id,)).fetchall()
    seen_map = {}
    for r in seen_rows:
        seen_map.setdefault(r['message_id'], []).append(r['user_id'])

    conn.commit()
    conn.close()

    member_map = {m['id']: m['name'] for m in members}

    return render_template(
        "chat.html", user=user, other=None, room=None, group=group,
        members=members, messages=messages, is_online=False,
        seen_map=seen_map, member_map=member_map,
        auto_delete_on=bool(group['auto_delete_24h']),
        auto_delete_allowed=auto_delete_allowed()
    )


@app.route("/group/<group_id>/toggle-auto-delete", methods=["POST"])
@login_required
def toggle_group_auto_delete(group_id):
    user = current_user()
    if not auto_delete_allowed():
        return jsonify({"error": "This feature has been disabled by the admin."}), 403
    conn = get_db()
    membership = conn.execute("SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                               (group_id, user['id'])).fetchone()
    if not membership:
        conn.close()
        return jsonify({"error": "Not a member."}), 403
    new_val = 1 if request.json.get("on") else 0
    conn.execute("UPDATE groups SET auto_delete_24h = ? WHERE id = ?", (new_val, group_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "auto_delete_on": bool(new_val)})


@app.route("/chat/<other_id>/toggle-auto-delete", methods=["POST"])
@login_required
def toggle_room_auto_delete(other_id):
    user = current_user()
    if not auto_delete_allowed():
        return jsonify({"error": "This feature has been disabled by the admin."}), 403
    room = room_name(user['id'], other_id)
    new_val = 1 if request.json.get("on") else 0
    conn = get_db()
    conn.execute("INSERT INTO rooms (room, auto_delete_24h) VALUES (?, ?) "
                 "ON CONFLICT(room) DO UPDATE SET auto_delete_24h = excluded.auto_delete_24h", (room, new_val))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "auto_delete_on": bool(new_val)})


# ---------------------------------------------------------------- Profile
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


# ---------------------------------------------------------------- Stories
@app.route("/stories/upload", methods=["POST"])
@login_required
def upload_story():
    user = current_user()
    file = request.files.get("file")
    caption = request.form.get("caption", "").strip()
    if not file or not file.filename:
        return jsonify({"error": "No file"}), 400

    ext = os.path.splitext(secure_filename(file.filename))[1]
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file.save(os.path.join(STORY_DIR, unique_name))
    media_type = "video" if file.mimetype.startswith("video/") else "image"

    now = datetime.utcnow()
    expires = now + timedelta(hours=24)
    conn = get_db()
    conn.execute("""
        INSERT INTO stories (user_id, media_url, media_type, caption, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user['id'], url_for('serve_story_media', filename=unique_name), media_type, caption,
          now.isoformat(), expires.isoformat()))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/stories/<user_id>")
@login_required
def get_user_stories(user_id):
    viewer = current_user()
    now = datetime.utcnow().isoformat()
    conn = get_db()
    owner = conn.execute("SELECT id, name, profile_pic, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if not owner or (owner['is_admin'] and not viewer['is_admin']):
        conn.close()
        return jsonify({"error": "not found"}), 404

    stories = conn.execute("""
        SELECT * FROM stories WHERE user_id = ? AND expires_at > ? ORDER BY created_at ASC
    """, (user_id, now)).fetchall()

    result = []
    for s in stories:
        conn.execute("INSERT OR IGNORE INTO story_views (story_id, viewer_id, viewed_at) VALUES (?, ?, ?)",
                     (s['id'], viewer['id'], datetime.utcnow().isoformat()))
        viewers = []
        if s['user_id'] == viewer['id']:
            vrows = conn.execute("""
                SELECT u.name, u.profile_pic FROM story_views sv JOIN users u ON u.id = sv.viewer_id
                WHERE sv.story_id = ? AND sv.viewer_id != ?
            """, (s['id'], viewer['id'])).fetchall()
            viewers = [{"name": v['name'], "pic": v['profile_pic']} for v in vrows]
        result.append({
            "id": s['id'], "media_url": s['media_url'], "media_type": s['media_type'],
            "caption": s['caption'], "created_at": s['created_at'], "viewers": viewers
        })
    conn.commit()
    conn.close()
    return jsonify({"owner": {"name": owner['name'], "pic": owner['profile_pic']}, "stories": result})


@app.route("/media/stories/<filename>")
def serve_story_media(filename):
    return send_from_directory(STORY_DIR, filename)


# ---------------------------------------------------------------- Admin (Haseeb Ashraf only)
@app.route("/admin")
@admin_required
def admin_panel():
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) c FROM users WHERE is_admin = 0").fetchone()['c']
    total_messages = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()['c']
    total_groups = conn.execute("SELECT COUNT(*) c FROM groups").fetchone()['c']

    users = conn.execute("""
        SELECT u.*, 
            (SELECT COUNT(*) FROM messages WHERE sender_id = u.id) as message_count
        FROM users u WHERE u.is_admin = 0 ORDER BY u.created_at DESC
    """).fetchall()

    conn.close()
    return render_template("admin.html", total_users=total_users, total_messages=total_messages,
                            total_groups=total_groups, admin_name=ADMIN_NAME, admin_email=ADMIN_EMAIL,
                            users=users, auto_delete_allowed=auto_delete_allowed())


@app.route("/admin/user/<user_id>/remove", methods=["POST"])
@admin_required
def admin_remove_user(user_id):
    conn = get_db()
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target or target['is_admin']:
        conn.close()
        return jsonify({"error": "Cannot remove this account."}), 400

    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.execute("DELETE FROM contacts WHERE user_id = ? OR contact_id = ?", (user_id, user_id))
    conn.execute("DELETE FROM group_members WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM messages WHERE sender_id = ? OR receiver_id = ?", (user_id, user_id))
    conn.execute("DELETE FROM stories WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    ONLINE_USERS.pop(user_id, None)
    return jsonify({"ok": True})


@app.route("/admin/user/<user_id>/ban", methods=["POST"])
@admin_required
def admin_ban_user(user_id):
    conn = get_db()
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target or target['is_admin']:
        conn.close()
        return jsonify({"error": "Cannot ban this account."}), 400
    new_val = 0 if target['is_banned'] else 1
    conn.execute("UPDATE users SET is_banned = ? WHERE id = ?", (new_val, user_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "banned": bool(new_val)})


@app.route("/admin/settings/auto-delete", methods=["POST"])
@admin_required
def admin_toggle_auto_delete_setting():
    new_val = "1" if request.json.get("on") else "0"
    set_setting("auto_delete_allowed", new_val)
    return jsonify({"ok": True, "on": new_val == "1"})


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
def _target_room(data):
    """Groups use their own group_id as the socket room; 1-1 chats use room_name."""
    return data.get('group_id') or data.get('room')


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
    join_room(_target_room(data))


@socketio.on('typing')
def on_typing(data):
    emit('typing', {"user_id": session.get('user_id')}, room=_target_room(data), include_self=False)


@socketio.on('stop_typing')
def on_stop_typing(data):
    emit('stop_typing', {"user_id": session.get('user_id')}, room=_target_room(data), include_self=False)


@socketio.on('send_message')
def on_send_message(data):
    user_id = session.get('user_id')
    if not user_id:
        return

    group_id = data.get('group_id', '')
    room = data.get('room', '') if not group_id else ''
    receiver_id = data.get('receiver_id', '') if not group_id else ''
    msg_type = data.get('type', 'text')
    content = data.get('content')
    filename = data.get('filename', '')
    view_once = 1 if data.get('view_once') else 0
    timestamp = datetime.utcnow().isoformat()

    conn = get_db()
    cur = conn.execute("""
        INSERT INTO messages (room, group_id, sender_id, receiver_id, type, content, filename, view_once, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (room, group_id, user_id, receiver_id, msg_type, content, filename, view_once, timestamp))
    msg_id = cur.lastrowid
    conn.commit()
    conn.close()

    payload = {
        "id": msg_id, "sender_id": user_id, "receiver_id": receiver_id, "group_id": group_id,
        "type": msg_type, "content": content, "filename": filename,
        "view_once": view_once, "viewed": 0, "seen": 0, "timestamp": timestamp
    }
    emit('receive_message', payload, room=group_id or room)


@socketio.on('mark_seen')
def on_mark_seen(data):
    viewer_id = session.get('user_id')
    conn = get_db()
    if data.get('group_id'):
        group_id = data['group_id']
        now = datetime.utcnow().isoformat()
        rows = conn.execute("SELECT id FROM messages WHERE group_id = ? AND sender_id != ?",
                             (group_id, viewer_id)).fetchall()
        for r in rows:
            conn.execute("INSERT OR IGNORE INTO message_seen_by (message_id, user_id, seen_at) VALUES (?, ?, ?)",
                         (r['id'], viewer_id, now))
        conn.commit()
        conn.close()
        emit('messages_seen', {"by": viewer_id, "group_id": group_id}, room=group_id, include_self=False)
    else:
        room = data['room']
        conn.execute("UPDATE messages SET seen = 1 WHERE room = ? AND receiver_id = ? AND seen = 0", (room, viewer_id))
        conn.commit()
        conn.close()
        emit('messages_seen', {"by": viewer_id, "room": room}, room=room, include_self=False)


@socketio.on('delete_message')
def on_delete_message(data):
    msg_id = data['message_id']
    target_room = _target_room(data)
    user_id = session.get('user_id')

    conn = get_db()
    msg = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if msg and msg['sender_id'] == user_id:
        conn.execute("UPDATE messages SET deleted = 1 WHERE id = ?", (msg_id,))
        conn.commit()
    conn.close()

    emit('message_deleted', {"message_id": msg_id}, room=target_room)


@socketio.on('view_once_opened')
def on_view_once_opened(data):
    msg_id = data['message_id']
    target_room = _target_room(data)
    conn = get_db()
    conn.execute("UPDATE messages SET viewed = 1 WHERE id = ?", (msg_id,))
    conn.commit()
    conn.close()
    emit('view_once_consumed', {"message_id": msg_id}, room=target_room)


@socketio.on('screenshot_alert')
def on_screenshot_alert(data):
    """
    Best-effort only: the browser cannot reliably detect real screenshots
    (especially on desktop/laptop -- there is no web API for this). This
    fires on a few weak signals (PrintScreen keypress, tab losing focus
    right after a shortcut) and simply notifies the other participant(s),
    the same way it would notify them of a *possible* screenshot -- it is
    not a guarantee and can be bypassed easily. See the chat UI notice.
    """
    user_id = session.get('user_id')
    target_room = _target_room(data)
    if not target_room:
        return
    emit('screenshot_alert', {"by": user_id}, room=target_room, include_self=False)


# ---------------------------------------------------------------- Background: 24h auto-delete
def auto_delete_worker():
    while True:
        try:
            cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
            conn = get_db()
            rooms_on = conn.execute("SELECT room FROM rooms WHERE auto_delete_24h = 1").fetchall()
            for r in rooms_on:
                conn.execute("UPDATE messages SET deleted = 1 WHERE room = ? AND timestamp < ? AND deleted = 0",
                             (r['room'], cutoff))
            groups_on = conn.execute("SELECT id FROM groups WHERE auto_delete_24h = 1").fetchall()
            for g in groups_on:
                conn.execute("UPDATE messages SET deleted = 1 WHERE group_id = ? AND timestamp < ? AND deleted = 0",
                             (g['id'], cutoff))
            # also clean up expired stories from disk-referencing rows (rows just age out of queries;
            # we leave the DB rows for history but they stop showing once expires_at has passed)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[auto_delete_worker error] {e}")
        time.sleep(300)  # check every 5 minutes


if __name__ == "__main__":
    init_db()
    t = threading.Thread(target=auto_delete_worker, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, debug=False, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)


# ----------------------------------------------------------------------------
# README (quick reference)
# ----------------------------------------------------------------------------
# 1) pip install -r requirements.txt
# 2) (optional but recommended) set these environment variables so
#    "Forgot Password" emails actually get delivered:
#       SMTP_HOST=smtp.gmail.com
#       SMTP_PORT=587
#       SMTP_USER=your_email@gmail.com
#       SMTP_PASS=your_16_char_app_password   (NOT your normal Gmail password --
#                                               create one at
#                                               https://myaccount.google.com/apppasswords)
#       SMTP_FROM=your_email@gmail.com
#    Without these, the reset code is printed to the server console and
#    also shown directly on the "Forgot Password" page, clearly labeled
#    as a dev-mode fallback, so you can still test the flow locally.
# 3) python app.py
# 4) First account signed up with haseebai541@gmail.com becomes admin
#    automatically. Change ADMIN_EMAIL above to your own email before
#    you deploy, if you want a different admin account.
# ----------------------------------------------------------------------------
