from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import sqlite3
import stripe
import os
import requests

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Change this in production for security

login_manager = LoginManager(app)
login_manager.login_view = 'login'

# API keys (test mode for Stripe; Grok key provided)
STRIPE_SECRET = 'sk_test_51RjlpnPTSyCDuRTk9VJHc2RS1qfhh11TCmeAtemt5Aj3I71QMrvHQyr0mmswJt72eiUpOwy4KC5nUCpmapHUyIt800lKisdD1V'
GROK_API_KEY = 'xai-pPu4kVvK3QQah3UN9FxEyOu95OsFkse4O32RfCWZxE85rfpyWjfqVk3AJbgc4Re8p7DlM5VjGRng7foa'
stripe.api_key = STRIPE_SECRET

# Database setup (SQLite for simplicity on Replit)
conn = sqlite3.connect('disputes.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, phone TEXT UNIQUE, verified INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS disputes (id INTEGER PRIMARY KEY, creator_id INTEGER, status TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS parties (id INTEGER PRIMARY KEY, dispute_id INTEGER, user_id INTEGER, submitted INTEGER, truth TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS resolutions (id INTEGER PRIMARY KEY, dispute_id INTEGER, verdict TEXT)''')
conn.commit()

class User(UserMixin):
    def __init__(self, id, phone):
        self.id = id
        self.phone = phone

@login_manager.user_loader
def load_user(user_id):
    c.execute('SELECT * FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    if row:
        return User(row[0], row[1])
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        phone = request.form['phone']
        c.execute('INSERT OR IGNORE INTO users (phone, verified) VALUES (?, 1)', (phone,))  # Directly verified
        conn.commit()
        c.execute('SELECT id FROM users WHERE phone=?', (phone,))
        user_id = c.fetchone()[0]
        user = User(user_id, phone)
        login_user(user)
        flash('Signed up and logged in successfully (verification skipped).')
        return redirect('/')
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form['phone']
        c.execute('SELECT * FROM users WHERE phone=?', (phone,))
        row = c.fetchone()
        if row:
            user = User(row[0], row[1])
            login_user(user)
            flash('Logged in successfully (verification skipped).')
            return redirect('/')
        flash('Please sign up first.')
    return render_template('signup.html')  # Reuse for login

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/')

@app.route('/create_dispute', methods=['GET', 'POST'])
@login_required
def create_dispute():
    if request.method == 'POST':
        c.execute('INSERT INTO disputes (creator_id, status) VALUES (?, "open")', (current_user.id,))
        dispute_id = c.lastrowid
        conn.commit()
        c.execute('INSERT INTO parties (dispute_id, user_id, submitted) VALUES (?, ?, 0)', (dispute_id, current_user.id,))
        conn.commit()
        return redirect(url_for('dispute', dispute_id=dispute_id))
    return render_template('create_dispute.html')

@app.route('/dispute/<int:dispute_id>', methods=['GET', 'POST'])
@login_required
def dispute(dispute_id):
    c.execute('SELECT * FROM parties WHERE dispute_id=? AND user_id=?', (dispute_id, current_user.id))
    if not c.fetchone():
        c.execute('INSERT INTO parties (dispute_id, user_id, submitted) VALUES (?, ?, 0)', (dispute_id, current_user.id,))
        conn.commit()
    c.execute('SELECT users.phone, parties.submitted FROM parties JOIN users ON users.id = parties.user_id WHERE dispute_id=?', (dispute_id,))
    parties = c.fetchall()
    c.execute('SELECT COUNT(*) FROM parties WHERE dispute_id=?', (dispute_id,))
    total = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM parties WHERE dispute_id=? AND submitted=1', (dispute_id,))
    subs = c.fetchone()[0]
    if subs == total and total > 1:
        generate_verdict(dispute_id)
    c.execute('SELECT verdict FROM resolutions WHERE dispute_id=?', (dispute_id,))
    verdict_row = c.fetchone()
    verdict = verdict_row[0] if verdict_row else None
    link = f'{request.host_url}dispute/{dispute_id}'
    return render_template('dispute.html', dispute_id=dispute_id, parties=parties, verdict=verdict, link=link)

@app.route('/dispute/join/<int:dispute_id>')
def join_dispute(dispute_id):
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    return redirect(url_for('dispute', dispute_id=dispute_id))

@app.route('/submit_truth/<int:dispute_id>', methods=['POST'])
@login_required
def submit_truth(dispute_id):
    truth = request.form['truth']
    try:
        charge = stripe.Charge.create(
            amount=100,  # $1 in cents
            currency='usd',
            description='Dispute submission',
            source=request.form['stripeToken']
        )
        c.execute('UPDATE parties SET submitted=1, truth=? WHERE dispute_id=? AND user_id=?', (truth, dispute_id, current_user.id))
        conn.commit()
        flash('Submitted and paid successfully.')
    except Exception as e:
        flash('Payment failed: ' + str(e))
    return redirect(url_for('dispute', dispute_id=dispute_id))

def generate_verdict(dispute_id):
    c.execute('SELECT truth FROM parties WHERE dispute_id=?', (dispute_id,))
    truths = [row[0] for row in c.fetchall() if row[0]]
    prompt = "Resolve this dispute fairly based on the following statements:\n" + "\n".join(f"Party {i+1}: {truth}" for i, truth in enumerate(truths))
    headers = {'Authorization': f'Bearer {GROK_API_KEY}', 'Content-Type': 'application/json'}
    data = {'model': 'grok', 'messages': [{'role': 'user', 'content': prompt}]}
    response = requests.post('https://api.x.ai/v1/chat/completions', headers=headers, json=data)
    verdict = response.json()['choices'][0]['message']['content']
    c.execute('INSERT INTO resolutions (dispute_id, verdict) VALUES (?, ?)', (dispute_id, verdict))
    conn.commit()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
