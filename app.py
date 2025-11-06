from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os, csv, io
from openpyxl import load_workbook
from PyPDF2 import PdfReader
import random
import pandas as pd

app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'devsecret')
os.makedirs(app.instance_path, exist_ok=True)
db_url = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url if db_url else 'sqlite:///' + os.path.join(app.instance_path, 'quiz.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)  # Ephemeral on Render; files are parsed then discarded

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ----------------------- Models -----------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(10), default='user')  # 'user' or 'admin'
    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    chapters = db.relationship('Chapter', backref='subject', lazy=True)
    questions = db.relationship('Question', backref='subject', lazy=True)

class Chapter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    questions = db.relationship('Question', backref='chapter', lazy=True)

class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    duration_minutes = db.Column(db.Integer, default=10)
    negative_marking = db.Column(db.Float, default=0.0)  # e.g., 0.25 for -0.25 per wrong
    questions = db.relationship('Question', backref='quiz', lazy=True)

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
    correct_option = db.Column(db.String(1), nullable=False)  # A/B/C/D

class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    user = db.relationship('User', backref='assignments')
    quiz = db.relationship('Quiz', backref='assignments')

class Attempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    score = db.Column(db.Float, default=0.0)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User', backref='attempts')
    quiz = db.relationship('Quiz', backref='attempts')

class AttemptAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey('attempt.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    selected_option = db.Column(db.String(1), nullable=True)
    is_correct = db.Column(db.Boolean, default=False)
    attempt = db.relationship('Attempt', backref='answers')
    question = db.relationship('Question')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ---- DB init for Flask 3.x
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', role='admin')
        admin.set_password('admin123')
        db.session.add(admin); db.session.commit()

# ----------------------- Helpers -----------------------
def get_or_create_subject(name):
    if not name: return None
    s = Subject.query.filter(db.func.lower(Subject.name)==name.lower()).first()
    if not s:
        s = Subject(name=name)
        db.session.add(s); db.session.commit()
    return s

def get_or_create_chapter(subject, name):
    if not name or not subject: return None
    c = Chapter.query.filter(db.func.lower(Chapter.name)==name.lower(), Chapter.subject_id==subject.id).first()
    if not c:
        c = Chapter(name=name, subject_id=subject.id)
        db.session.add(c); db.session.commit()
    return c

# ----------------------- Routes -----------------------
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        u = User.query.filter_by(username=username).first()
        if u and u.check_password(password):
            login_user(u)
            return redirect(url_for('index'))
        flash('Invalid credentials','error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user(); return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    if current_user.role == 'admin':
        # Quick stats
        stats = {
            "users": User.query.count(),
            "quizzes": Quiz.query.count(),
            "questions": Question.query.count(),
            "attempts": Attempt.query.count(),
        }
        recent_attempts = Attempt.query.order_by(Attempt.started_at.desc()).limit(10).all()
        subjects = Subject.query.order_by(Subject.name.asc()).all()
        return render_template('admin_dashboard.html', stats=stats, recent_attempts=recent_attempts, subjects=subjects, quizzes=Quiz.query.all(), users=User.query.all())
    # user
    assignments = Assignment.query.filter_by(user_id=current_user.id).all()
    attempts = Attempt.query.filter_by(user_id=current_user.id).order_by(Attempt.started_at.desc()).all()
    return render_template('user_dashboard.html', assignments=assignments, attempts=attempts)

# ----- Admin management -----
@app.route('/admin/create_user', methods=['POST'])
@login_required
def admin_create_user():
    if current_user.role != 'admin':
        flash('Unauthorized','error'); return redirect(url_for('index'))
    username = request.form.get('username','').strip()
    password = request.form.get('password','')
    role = request.form.get('role','user')
    if not username or not password:
        flash('Username/password required','error'); return redirect(url_for('index'))
    if User.query.filter_by(username=username).first():
        flash('User already exists','error'); return redirect(url_for('index'))
    u = User(username=username, role=role)
    u.set_password(password)
    db.session.add(u); db.session.commit()
    flash('User created','success'); return redirect(url_for('index'))

@app.route('/admin/create_quiz', methods=['POST'])
@login_required
def admin_create_quiz():
    if current_user.role != 'admin':
        flash('Unauthorized','error'); return redirect(url_for('index'))
    title = request.form.get('title','').strip()
    duration = request.form.get('duration','10').strip()
    negative = request.form.get('negative','0').strip()
    try: duration = int(duration)
    except: duration = 10
    try: negative = float(negative)
    except: negative = 0.0
    if not title:
        flash('Title required','error'); return redirect(url_for('index'))
    qz = Quiz(title=title, duration_minutes=duration, negative_marking=negative)
    db.session.add(qz); db.session.commit()
    flash('Quiz created','success'); return redirect(url_for('index'))

@app.route('/admin/assign', methods=['POST'])
@login_required
def admin_assign_quiz():
    if current_user.role != 'admin':
        flash('Unauthorized','error'); return redirect(url_for('index'))
    user_id = request.form.get('user_id','').strip()
    quiz_id = request.form.get('quiz_id','').strip()
    if not user_id.isdigit() or not quiz_id.isdigit():
        flash('Select valid user/quiz','error'); return redirect(url_for('index'))
    if Assignment.query.filter_by(user_id=int(user_id), quiz_id=int(quiz_id)).first():
        flash('Already assigned','warning'); return redirect(url_for('index'))
    a = Assignment(user_id=int(user_id), quiz_id=int(quiz_id))
    db.session.add(a); db.session.commit()
    flash('Assigned successfully','success'); return redirect(url_for('index'))

@app.route('/admin/upload_questions', methods=['POST'])
@login_required
def admin_upload_questions():
    if current_user.role != 'admin':
        flash('Unauthorized','error'); return redirect(url_for('index'))
    qid_raw = request.form.get('quiz_id','').strip()
    if not qid_raw.isdigit():
        flash('Please select a quiz','error'); return redirect(url_for('index'))
    quiz = Quiz.query.get(int(qid_raw))
    if not quiz:
        flash('Quiz not found','error'); return redirect(url_for('index'))
    f = request.files.get('file')
    if not f or f.filename == '':
        flash('Choose a file','error'); return redirect(url_for('index'))
    fp = os.path.join(app.config['UPLOAD_FOLDER'], f.filename)
    f.save(fp)
    ext = f.filename.lower().rsplit('.',1)[-1]
    added = 0
    try:
        if ext == 'csv':
            with open(fp, newline='', encoding='utf-8') as cf:
                reader = csv.DictReader(cf)
                for row in reader:
                    subj = get_or_create_subject((row.get('subject') or '').strip()) if row.get('subject') else None
                    chap = get_or_create_chapter(subj, (row.get('chapter') or '').strip()) if (subj and row.get('chapter')) else None
                    qtext = row.get('question') or row.get('Question')
                    A = row.get('option_a') or row.get('A') or row.get('Option_A')
                    B = row.get('option_b') or row.get('B') or row.get('Option_B')
                    C = row.get('option_c') or row.get('C') or row.get('Option_C')
                    D = row.get('option_d') or row.get('D') or row.get('Option_D')
                    ans = (row.get('correct_option') or row.get('Answer') or '').strip().upper()[:1]
                    if qtext and A and B and C and D and ans in {'A','B','C','D'}:
                        db.session.add(Question(quiz_id=quiz.id, subject_id=(subj.id if subj else None), chapter_id=(chap.id if chap else None),
                                                text=str(qtext), option_a=str(A), option_b=str(B), option_c=str(C), option_d=str(D), correct_option=ans))
                        added += 1
        elif ext == 'xlsx':
            wb = load_workbook(fp); sh = wb.active
            headers = [c.value.strip().lower() if isinstance(c.value,str) else '' for c in next(sh.iter_rows(min_row=1, max_row=1))]
            idx = {h:i for i,h in enumerate(headers)}
            def gv(row, key):
                i = idx.get(key)
                return (row[i].value if i is not None else None)
            for row in sh.iter_rows(min_row=2):
                sub = gv(row,'subject'); chapn = gv(row,'chapter')
                subj = get_or_create_subject(str(sub).strip()) if sub else None
                chap = get_or_create_chapter(subj, str(chapn).strip()) if (subj and chapn) else None
                qtext = gv(row,'question'); A = gv(row,'option_a'); B = gv(row,'option_b'); C = gv(row,'option_c'); D = gv(row,'option_d')
                ans_val = gv(row,'correct_option'); ans = str(ans_val).strip().upper()[:1] if ans_val is not None else ''
                if qtext and A and B and C and D and ans in {'A','B','C','D'}:
                    db.session.add(Question(quiz_id=quiz.id, subject_id=(subj.id if subj else None), chapter_id=(chap.id if chap else None),
                                            text=str(qtext), option_a=str(A), option_b=str(B), option_c=str(C), option_d=str(D), correct_option=ans))
                    added += 1
        elif ext == 'pdf':
            reader = PdfReader(fp); content = ""
            for page in reader.pages:
                try: content += page.extract_text() + "\n"
                except: pass
            blocks = [b.strip() for b in content.split('\n\n') if b.strip()]
            for b in blocks:
                lines = [l.strip() for l in b.splitlines() if l.strip()]
                if len(lines) < 6: continue
                qline = lines[0]; qtext = qline[2:].strip() if qline.lower().startswith('q:') else qline
                opts = {'A':'','B':'','C':'','D':''}
                for ln in lines[1:5]:
                    up = ln[:2].upper()
                    if up.startswith('A)'): opts['A'] = ln[2:].strip()
                    if up.startswith('B)'): opts['B'] = ln[2:].strip()
                    if up.startswith('C)'): opts['C'] = ln[2:].strip()
                    if up.startswith('D)'): opts['D'] = ln[2:].strip()
                ans_line = next((ln for ln in lines if ln.upper().startswith('ANS')), '')
                ans = ans_line.split(':')[-1].strip().upper()[:1] if ':' in ans_line else ''
                if qtext and all(opts.values()) and ans in {'A','B','C','D'}:
                    db.session.add(Question(quiz_id=quiz.id, text=qtext, option_a=opts['A'], option_b=opts['B'], option_c=opts['C'], option_d=opts['D'], correct_option=ans))
                    added += 1
        else:
            flash('Unsupported file type','error'); return redirect(url_for('index'))
        db.session.commit()
        flash(f'Added {added} questions to \"{quiz.title}\"','success')
    except Exception as e:
        db.session.rollback(); flash(f'Upload error: {e}','error')
    return redirect(url_for('index'))

# ----- Question bank filter (Admin) -----
@app.route('/admin/questions')
@login_required
def admin_questions():
    if current_user.role != 'admin':
        flash('Unauthorized','error'); return redirect(url_for('index'))
    subject_id = request.args.get('subject_id', type=int)
    chapter_id = request.args.get('chapter_id', type=int)
    q = Question.query
    subjects = Subject.query.order_by(Subject.name.asc()).all()
    chapters = []
    if subject_id:
        q = q.filter_by(subject_id=subject_id)
        chapters = Chapter.query.filter_by(subject_id=subject_id).order_by(Chapter.name.asc()).all()
    if chapter_id:
        q = q.filter_by(chapter_id=chapter_id)
    items = q.order_by(Question.id.desc()).limit(500).all()
    return render_template('admin_questions.html', items=items, subjects=subjects, subject_id=subject_id, chapters=chapters, chapter_id=chapter_id)

# ----- Export attempts CSV -----
@app.route('/admin/export_attempts')
@login_required
def export_attempts():
    if current_user.role != 'admin':
        flash('Unauthorized','error'); return redirect(url_for('index'))
    rows = []
    for a in Attempt.query.order_by(Attempt.started_at.desc()).all():
        rows.append({"attempt_id": a.id, "user": a.user.username, "quiz": a.quiz.title, "score": a.score, "started_at": a.started_at, "submitted_at": a.submitted_at})
    df = pd.DataFrame(rows)
    buf = io.StringIO(); df.to_csv(buf, index=False); buf.seek(0)
    return send_file(io.BytesIO(buf.getvalue().encode('utf-8')), mimetype='text/csv', as_attachment=True, download_name='attempts.csv')

# ---------- Start quiz, randomize order ----------
@app.route('/quiz/<int:quiz_id>/start')
@login_required
def start_quiz(quiz_id):
    if current_user.role != 'admin':
        if not Assignment.query.filter_by(user_id=current_user.id, quiz_id=quiz_id).first():
            flash('Quiz not assigned','error'); return redirect(url_for('index'))
    quiz = Quiz.query.get_or_404(quiz_id)
    at = Attempt(user_id=current_user.id, quiz_id=quiz.id, started_at=datetime.utcnow())
    db.session.add(at); db.session.commit()
    questions = Question.query.filter_by(quiz_id=quiz.id).all()
    random.shuffle(questions)
    deadline = datetime.utcnow() + timedelta(minutes=quiz.duration_minutes)
    deadline_iso = deadline.isoformat() + 'Z'
    return render_template('take_quiz.html', quiz=quiz, questions=questions, deadline_iso=deadline_iso, attempt_id=at.id)

# ---------- Submit quiz and record answers ----------
@app.route('/quiz/<int:quiz_id>/submit', methods=['POST'])
@login_required
def submit_quiz(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    attempt_id = request.form.get('attempt_id', type=int)
    at = Attempt.query.get(attempt_id) if attempt_id else None
    if not at or at.user_id != current_user.id or at.submitted_at:
        # fallback: latest
        at = Attempt.query.filter_by(user_id=current_user.id, quiz_id=quiz.id, submitted_at=None).order_by(Attempt.started_at.desc()).first()
        if not at:
            at = Attempt(user_id=current_user.id, quiz_id=quiz.id, started_at=datetime.utcnow())
            db.session.add(at); db.session.commit()
    questions = Question.query.filter_by(quiz_id=quiz.id).all()
    score = 0.0
    for q in questions:
        sel = request.form.get(f'q_{q.id}', None)
        correct = (sel is not None and sel.upper()[:1] == q.correct_option)
        if correct:
            score += 1
        else:
            if sel is not None and quiz.negative_marking > 0:
                score -= quiz.negative_marking
        ans = AttemptAnswer(attempt_id=at.id, question_id=q.id, selected_option=(sel.upper()[:1] if sel else None), is_correct=bool(correct))
        db.session.add(ans)
    at.score = score
    at.submitted_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('review_attempt', attempt_id=at.id))

# ---------- Review page ----------
@app.route('/attempt/<int:attempt_id>/review')
@login_required
def review_attempt(attempt_id):
    at = Attempt.query.get_or_404(attempt_id)
    if current_user.role != 'admin' and at.user_id != current_user.id:
        flash('Unauthorized','error'); return redirect(url_for('index'))
    # Pair answers with questions
    answers = AttemptAnswer.query.filter_by(attempt_id=at.id).all()
    # Ensure order by question id
    answers.sort(key=lambda a: a.question_id)
    return render_template('review.html', attempt=at, answers=answers)

# ---------- Result page ----------
@app.route('/result/<int:attempt_id>')
@login_required
def show_result(attempt_id):
    at = Attempt.query.get_or_404(attempt_id)
    if current_user.role != 'admin' and at.user_id != current_user.id:
        flash('Unauthorized','error'); return redirect(url_for('index'))
    return render_template('result.html', attempt=at)

if __name__ == '__main__':
    app.run(debug=True)
