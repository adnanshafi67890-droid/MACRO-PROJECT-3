import os
import re
import json
import sqlite3
import urllib.request
from datetime import datetime
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'realtime-chat-key-2026'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

connected_users = {}
DB_NAME = "users.db"

def init_db():
    """Initializes SQLite database and verifies schema."""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            profile_pic TEXT DEFAULT ''
        )
    ''')
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'profile_pic' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT DEFAULT ''")
        
    conn.commit()
    conn.close()

init_db()

from dotenv import load_dotenv
from google import genai

# Load variables from .env file
load_dotenv()

def get_ai_response(prompt):
    """Generates AI chatbot responses using the official Gemini Python SDK."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"You are a helpful AI assistant inside a real-time chat app for computer science students. Keep answers concise, clear, and informative.\nUser prompt: {prompt}"
            )
            return response.text.strip()
        except Exception as e:
            print(f"[GEMINI SDK ERROR] {e}")

    # Fallback responder if API key is missing or encounters an issue
    p_lower = prompt.lower()
    if any(g in p_lower for g in ["hi", "hello", "hey"]):
        return "Hello! How can I assist you with your projects, study topics, or code today?"
    elif "help" in p_lower:
        return "Mention `@ai <your question>` anywhere in public or private chat to ask me anything!"
    else:
        return f"🤖 [AI Assistant]: Received query '{prompt[:30]}...'. Please ensure `GEMINI_API_KEY` is configured in your `.env` file."

    # Fallback smart responder if GEMINI_API_KEY is not set or request times out
    p_lower = prompt.lower()
    if any(greeting in p_lower for greeting in ["hi", "hello", "hey", "greetings"]):
        return "Hello! I am your AI Assistant. How can I assist you with your projects, study topics, or code today?"
    elif "help" in p_lower or "command" in p_lower:
        return "You can invoke me anytime in chat by typing `@ai <your prompt or question>`!"
    elif "code" in p_lower or "python" in p_lower or "flask" in p_lower:
        return "Flask and WebSockets allow real-time full-duplex communication over a single TCP connection, ideal for chat apps and live feeds!"
    elif "time" in p_lower or "date" in p_lower:
        return f"Server time is currently {datetime.now().strftime('%I:%M %p, %B %d, %Y')}."
    else:
        return f"🤖 [AI Assistant]: Received your query regarding '{prompt[:40]}...'. (Tip: Set the `GEMINI_API_KEY` environment variable in your terminal to enable live LLM integration!)"

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print(f"\n[SOCKET CONNECTED] Session ID: {request.sid}")

@socketio.on('register_account')
def handle_register_account(data):
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    if not username or not password:
        emit('auth_response', {'success': False, 'action': 'register', 'message': 'Username and password required.'})
        return

    if len(password) < 4:
        emit('auth_response', {'success': False, 'action': 'register', 'message': 'Password must be at least 4 characters.'})
        return

    hashed_password = generate_password_hash(password)

    try:
        conn = sqlite3.connect(DB_NAME, timeout=10)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (username, password_hash, profile_pic) VALUES (?, ?, ?)", (username, hashed_password, ''))
        conn.commit()
        conn.close()
        
        emit('auth_response', {
            'success': True, 
            'action': 'register', 
            'message': 'Account created successfully! Please log in.'
        })
    except sqlite3.IntegrityError:
        emit('auth_response', {
            'success': False, 
            'action': 'register', 
            'message': 'Username already exists. Choose another.'
        })

@socketio.on('login_account')
def handle_login_account(data):
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        emit('auth_response', {'success': False, 'action': 'login', 'message': 'Fill in all fields.'})
        return

    conn = sqlite3.connect(DB_NAME, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash, profile_pic FROM users WHERE LOWER(username) = ?", (username.lower(),))
    row = cursor.fetchone()
    conn.close()

    if row and check_password_hash(row[0], password):
        profile_pic = row[1] if row[1] else ''
        
        stale_sids = [sid for sid, user in connected_users.items() if user['username'].lower() == username.lower()]
        for sid in stale_sids:
            connected_users.pop(sid, None)

        connected_users[request.sid] = {
            'username': username,
            'profile_pic': profile_pic
        }
        
        emit('auth_response', {
            'success': True, 
            'action': 'login', 
            'username': username,
            'profile_pic': profile_pic
        })
        
        emit('chat_message', {
            'type': 'SYSTEM',
            'sender': 'SERVER',
            'content': f"{username} joined the chat.",
            'timestamp': datetime.now().strftime("%I:%M %p")
        }, broadcast=True)
        
        broadcast_user_list()
    else:
        emit('auth_response', {'success': False, 'action': 'login', 'message': 'Invalid username or password.'})

@socketio.on('update_profile_pic')
def handle_update_profile_pic(data):
    user = connected_users.get(request.sid)
    if not user:
        return
    
    image_data = data.get('image', '')
    username = user['username']
    
    conn = sqlite3.connect(DB_NAME, timeout=10)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET profile_pic = ? WHERE LOWER(username) = ?", (image_data, username.lower()))
    conn.commit()
    conn.close()
    
    user['profile_pic'] = image_data
    emit('profile_pic_updated', {'success': True, 'image': image_data})
    broadcast_user_list()

@socketio.on('typing')
def handle_typing(data):
    user = connected_users.get(request.sid)
    if not user:
        return
    
    is_typing = data.get('is_typing', False)
    recipient = data.get('recipient', 'ALL')
    
    if recipient == 'ALL':
        emit('user_typing', {
            'username': user['username'],
            'is_typing': is_typing,
            'recipient': 'ALL'
        }, broadcast=True, include_self=False)
    else:
        recipient_sid = None
        for sid, u in connected_users.items():
            if u['username'].lower() == recipient.lower():
                recipient_sid = sid
                break
        if recipient_sid:
            emit('user_typing', {
                'username': user['username'],
                'is_typing': is_typing,
                'recipient': recipient
            }, room=recipient_sid)

@socketio.on('public_message')
def handle_public_message(data):
    user = connected_users.get(request.sid)
    if not user:
        return
    
    content = data.get('content', '').strip()
    reply_to = data.get('reply_to', None)
    timestamp = datetime.now().strftime("%I:%M %p")
    
    if content:
        emit('chat_message', {
            'type': 'BROADCAST',
            'sender': user['username'],
            'content': content,
            'reply_to': reply_to,
            'timestamp': timestamp
        }, broadcast=True)

        # Trigger @ai Bot if mentioned in public chat
        if '@ai' in content.lower():
            prompt = re.sub(r'(?i)@ai', '', content).strip()
            if not prompt:
                ai_text = "Hello! Mention `@ai <your question>` in chat to ask me anything."
            else:
                ai_text = get_ai_response(prompt)
            
            emit('chat_message', {
                'type': 'AI_BOT',
                'sender': '🤖 AI Assistant',
                'content': ai_text,
                'reply_to': {'sender': user['username'], 'content': content},
                'timestamp': datetime.now().strftime("%I:%M %p")
            }, broadcast=True)

@socketio.on('private_message')
def handle_private_message(data):
    sender = connected_users.get(request.sid)
    if not sender:
        return

    target_username = data.get('recipient', '').strip()
    content = data.get('content', '').strip()
    reply_to = data.get('reply_to', None)
    timestamp = datetime.now().strftime("%I:%M %p")
    
    if not target_username or not content:
        return

    recipient_sid = None
    for sid, user_data in connected_users.items():
        if user_data['username'].lower() == target_username.lower():
            recipient_sid = sid
            break

    if recipient_sid:
        emit('chat_message', {
            'type': 'DIRECT',
            'sender': sender['username'],
            'recipient': target_username,
            'content': content,
            'reply_to': reply_to,
            'timestamp': timestamp
        }, room=recipient_sid)
        
        emit('chat_message', {
            'type': 'DIRECT_SENT',
            'sender': sender['username'],
            'recipient': target_username,
            'content': content,
            'reply_to': reply_to,
            'timestamp': timestamp
        }, room=request.sid)

        # Trigger @ai Bot if mentioned inside direct message
        if '@ai' in content.lower():
            prompt = re.sub(r'(?i)@ai', '', content).strip()
            ai_text = get_ai_response(prompt if prompt else "Hello!")
            emit('chat_message', {
                'type': 'AI_BOT',
                'sender': '🤖 AI Assistant',
                'content': ai_text,
                'reply_to': {'sender': sender['username'], 'content': content},
                'timestamp': datetime.now().strftime("%I:%M %p")
            }, room=request.sid)
    else:
        emit('chat_message', {
            'type': 'SYSTEM',
            'sender': 'SERVER',
            'content': f"User '{target_username}' is offline.",
            'timestamp': timestamp
        }, room=request.sid)

@socketio.on('logout_account')
def handle_logout_account():
    user = connected_users.pop(request.sid, None)
    if user:
        username = user['username']
        emit('chat_message', {
            'type': 'SYSTEM',
            'sender': 'SERVER',
            'content': f"{username} logged out.",
            'timestamp': datetime.now().strftime("%I:%M %p")
        }, broadcast=True)
        broadcast_user_list()
    emit('logout_response', {'success': True})

@socketio.on('delete_account')
def handle_delete_account():
    user = connected_users.get(request.sid)
    if not user:
        return

    username = user['username']

    conn = sqlite3.connect(DB_NAME, timeout=10)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE LOWER(username) = ?", (username.lower(),))
    conn.commit()
    conn.close()

    connected_users.pop(request.sid, None)

    emit('account_deleted', {'success': True, 'message': 'Your account has been permanently deleted.'})

    emit('chat_message', {
        'type': 'SYSTEM',
        'sender': 'SERVER',
        'content': f"{username} permanently deleted their account.",
        'timestamp': datetime.now().strftime("%I:%M %p")
    }, broadcast=True)

    broadcast_user_list()

@socketio.on('disconnect')
def handle_disconnect():
    user = connected_users.pop(request.sid, None)
    if user:
        username = user['username']
        print(f"[DISCONNECTED] User: {username}")
        emit('chat_message', {
            'type': 'SYSTEM',
            'sender': 'SERVER',
            'content': f"{username} left the chat.",
            'timestamp': datetime.now().strftime("%I:%M %p")
        }, broadcast=True)
        broadcast_user_list()

def broadcast_user_list():
    users = sorted(list({u['username'] for u in connected_users.values()}))
    emit('update_user_list', {'users': users}, broadcast=True)

if __name__ == '__main__':
    print("\n[SERVER ACTIVE] Running Deep Space Midnight Chat Engine on http://0.0.0.0:5000\n")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)