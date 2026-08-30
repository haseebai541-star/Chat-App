"""
Real-Time Chat App (WhatsApp-jaisi, Phase 1)
----------------------------------------------
Features:
  - Link share kar ke koi bhi ek room mein aa kar chat kar sakta hai
  - Real-time text messages
  - Voice note record + bhejna
  - Image / video / file sharing

Requirements:
    pip install flask flask-socketio

Run:
    python app.py

Phir browser mein: http://127.0.0.1:5000
Naya chat room banane ke liye "Naya Chat Shuru Karein" button dabayein,
jo link milega wo doosre insaan ko bhej dein - wo click karega to
seedha usi chat room mein aa jayega.
"""

import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-this-secret-key'
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

socketio = SocketIO(app, max_http_buffer_size=50 * 1024 * 1024, cors_allowed_origins="*")

ROOM_MESSAGES = {}


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/new-chat")
def new_chat():
    room_id = str(uuid.uuid4())[:8]
    return redirect(url_for("chat_room", room_id=room_id))


@app.route("/room/<room_id>")
def chat_room(room_id):
    return render_template("chat.html", room_id=room_id)


@app.route("/upload/<room_id>", methods=["POST"])
def upload_file(room_id):
    if 'file' not in request.files:
        return {"error": "Koi file nahi mili"}, 400

    file = request.files['file']
    ext = os.path.splitext(file.filename)[1]
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    file.save(save_path)

    file_url = url_for('static', filename=f'uploads/{unique_name}')
    return {"url": file_url, "filename": file.filename}


@socketio.on('join')
def on_join(data):
    room = data['room']
    username = data.get('username', 'Guest')
    join_room(room)
    emit('status', {"msg": f"{username} chat mein shamil ho gaya/gayi hai"}, room=room)


@socketio.on('send_message')
def on_send_message(data):
    room = data['room']
    message = {
        "username": data.get('username', 'Guest'),
        "type": data.get('type', 'text'),
        "content": data.get('content'),
        "filename": data.get('filename', ''),
    }
    ROOM_MESSAGES.setdefault(room, []).append(message)
    emit('receive_message', message, room=room)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, debug=False, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
