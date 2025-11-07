
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from sqlalchemy import inspect, text
import os, csv, io, random, json
from openpyxl import load_workbook
from reportlab.pdfgen import canvas

app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'devsecret')
os.makedirs(app.instance_path, exist_ok=True)

# --------- DB config ---------
db_url = os.environ.get('DATABASE_URL')
if db_url:
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
else:
    db_url = 'sqlite:///' + os.path.join(app.instance_path, 'quiz.db')

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ---------------- Models ----------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='user')
    coins = db.Column(db.Integer, default=0)
    badges = db.Column(db.String(200), default='')
    streak = db.Column(db.Integer, default=0)
    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)

class Chapter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    subject = db.relationship('Subject', backref='chapters')

class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    duration_minutes = db.Column(db.Integer, default=10)
    negative_marking = db.Column(db.Float, default=0.0)
    marks_per_question = db.Column(db.Float, default=1.0)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    questions = db.relationship('Question', backref='quiz', cascade="all, delete-orphan")

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey('chapter.id'), nullable=True)
    text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.Text, nullable=False)
    option_b = db.Column(db.Text, nullable=False)
    option_c = db.Column(db.Text, nullable=False)
    option_d = db.Column(db.Text, nullable=False)
    correct_option = db.Column(db.String(1), nullable=False)
    difficulty = db.Column(db.String(10), default='Medium')
    qtype = db.Column(db.String(20), default='Concept')
    subject = db.relationship('Subject')
    chapter = db.relationship('Chapter')

class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    attempts_limit = db.Column(db.Integer, default=1)
    cooldown_days = db.Column(db.Integer, default=0)
    user = db.relationship('User', backref='assignments')
    quiz = db.relationship('Quiz', backref='assignments')

class Attempt(db.Model):
    id = db.Column(db.Integer, primary key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    score = db.Column(db.Float, default=0.0)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)
    active = db.Column(db.Boolean, default=True)
    user = db.relationship('User', backref='attempts')
    quiz = db.relationship('Quiz', backref='attempts')

class AttemptAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey('attempt.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    selected_option = db.Column(db.String(1), nullable=True)
    is_correct = db.Column(db.Boolean, default=False)
    marks_earned = db.Column(db.Float, default=0.0)
    flagged = db.Column(db.Boolean, default=False)
    note = db.Column(db.Text, default='')
    attempt = db.relationship('Attempt', backref='answers')
    question = db.relationship('Question')

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    from_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class BlueprintSpec(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    spec_json = db.Column(db.Text, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def auto_migrate_user_table():
    # Handle reserved table name on Postgres and per-statement commit/rollback
    insp = inspect(db.engine)
    cols = {c['name'] for c in insp.get_columns('user')}
    dialect = db.engine.dialect.name
    tbl = '"user"' if dialect == 'postgresql' else 'user'
    ine = 'IF NOT EXISTS ' if dialect == 'postgresql' else ''

    stmts = []
    if 'coins' not in cols:   stmts.append(f'ALTER TABLE {tbl} ADD COLUMN {ine} coins INTEGER DEFAULT 0')
    if 'badges' not in cols:  stmts.append(f"ALTER TABLE {tbl} ADD COLUMN {ine} badges VARCHAR(200) DEFAULT ''")
    if 'streak' not in cols:  stmts.append(f'ALTER TABLE {tbl} ADD COLUMN {ine} streak INTEGER DEFAULT 0')

    for s in stmts:
        try:
            db.session.execute(text(s))
            db.session.commit()
        except Exception as e:
            db.session.rollback()  # clear aborted transaction
            # ignore if the column already exists or IF NOT EXISTS not supported (sqlite)
            # we re-check presence to be safe
            insp2 = inspect(db.engine)
            cols2 = {c['name'] for c in insp2.get_columns('user')}
            # if still missing and dialect is sqlite without IF NOT EXISTS, try plain add
            if 'coins' not in cols2 or 'badges' not in cols2 or 'streak' not in cols2:
                pass  # column might be added by earlier statements; continue

with app.app_context():
    db.create_all()
    auto_migrate_user_table()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', role='admin'); admin.set_password('admin123')
        db.session.add(admin); db.session.commit()

# --------- minimal routes to keep file concise (same as previous build) ---------
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form.get('username','').strip()).first()
        if u and u.check_password(request.form.get('password','')):
            login_user(u); return redirect(url_for('index'))
        flash('Invalid credentials','error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user(); return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    stats = dict(users=User.query.count())
    return render_template('admin_dashboard.html', stats=stats, users=User.query.all(), quizzes=[],
                           blueprints=[], leaderboard=[], active=[])

@app.route('/admin/add_user', methods=['POST'])
@login_required
def admin_add_user():
    username = request.form.get('username','').strip()
    password = request.form.get('password','').strip()
    role = request.form.get('role','user')
    if not username or not password:
        flash('Username & password required', 'error'); return redirect(url_for('index'))
    if User.query.filter_by(username=username).first():
        flash('User already exists', 'error'); return redirect(url_for('index'))
    u = User(username=username, role=role); u.set_password(password); db.session.add(u); db.session.commit()
    flash('User created', 'success'); return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
