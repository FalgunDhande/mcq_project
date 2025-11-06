from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os, csv, io, random
from openpyxl import load_workbook
from PyPDF2 import PdfReader

app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'devsecret')
os.makedirs(app.instance_path, exist_ok=True)

# Use Postgres on Render if present, else SQLite for local
db_url = os.environ.get('DATABASE_URL')
if db_url:
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif db_url.startswith('postgresql://'):
        db_url = db_url.replace('postgresql://', 'postgresql+psycopg://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url if db_url else 'sqlite:///' + os.path.join(app.instance_path, 'quiz.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)  # Ephemeral on Render

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

class Chapter(db.Model):  # Topic
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    questions = db.relationship('Question', backref='chapter', lazy=True)

class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    duration_minutes = db.Column(db.Integer, default=10)
    negative_marking = db.Column(db.Float, default=0.0)  # e.g., 0.25 for -0.25 per wrong
    marks_per_question = db.Column(db.Float, default=1.0)
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
    marks_earned = db.Column(db.Float, default=0.0)
    attempt = db.relationship('Attempt', backref='answers')
    question = db.relationship('Question')

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ---- DB init (Flask 3) ----
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
        stats = {
            "users": User.query.count(),
            "quizzes": Quiz.query.count(),
            "questions": Question.query.count(),
            "attempts": Attempt.query.count(),
        }
        recent_attempts = Attempt.query.order_by(Attempt.started_at.desc()).limit(10).all()
        return render_template('admin_dashboard.html', stats=stats, recent_attempts=recent_attempts, quizzes=Quiz.query.all(), users=User.query.all(), subjects=Subject.query.order_by(Subject.name.asc()).all())
    assignments = Assignment.query.filter_by(user_id=current_user.id).all()
    attempts = Attempt.query.filter_by(user_id=current_user.id).order_by(Attempt.started_at.desc()).all()
    return render_template('user_dashboard.html', assignments=assignments, attempts=attempts)

# ---------- Admin: manage users (list + delete) ----------
@app.route('/admin/users')
@login_required
def admin_users():
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    users = User.query.order_by(User.username.asc()).all()
    return render_template('admin_users.html', users=users, total=len(users))

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    if current_user.id == user_id:
        flash("You can't delete yourself.", "error")
        return redirect(url_for('admin_users'))
    u = db.session.get(User, user_id)
    if not u:
        flash('User not found','error'); return redirect(url_for('admin_users'))
    # Cascade manual: delete attempt answers -> attempts -> assignments -> user
    AttemptAnswer.query.filter(AttemptAnswer.attempt_id.in_(db.session.query(Attempt.id).filter_by(user_id=u.id))).delete(synchronize_session=False)
    Attempt.query.filter_by(user_id=u.id).delete(synchronize_session=False)
    Assignment.query.filter_by(user_id=u.id).delete(synchronize_session=False)
    db.session.delete(u)
    db.session.commit()
    flash('User deleted','success')
    return redirect(url_for('admin_users'))

# ---------- Admin: create user ----------
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

# ---------- Admin: create quiz ----------
@app.route('/admin/create_quiz', methods=['POST'])
@login_required
def admin_create_quiz():
    if current_user.role != 'admin':
        flash('Unauthorized','error'); return redirect(url_for('index'))
    title = request.form.get('title','').strip()
    duration = request.form.get('duration','10').strip()
    negative = request.form.get('negative','0').strip()
    marks = request.form.get('marks','1').strip()
    try: duration = int(duration)
    except: duration = 10
    try: negative = float(negative)
    except: negative = 0.0
    try: marks = float(marks)
    except: marks = 1.0
    if not title:
        flash('Title required','error'); return redirect(url_for('index'))
    qz = Quiz(title=title, duration_minutes=duration, negative_marking=negative, marks_per_question=marks)
    db.session.add(qz); db.session.commit()
    flash('Quiz created','success'); return redirect(url_for('index'))

# ---------- Admin: assign quiz ----------
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

# ---------- Admin: upload questions (bulk) ----------
@app.route('/admin/upload_questions', methods=['POST'])
@login_required
def admin_upload_questions():
    if current_user.role != 'admin':
        flash('Unauthorized','error'); return redirect(url_for('index'))
    qid_raw = request.form.get('quiz_id','').strip()
    if not qid_raw.isdigit():
        flash('Please select a quiz','error'); return redirect(url_for('index'))
    quiz = db.session.get(Quiz, int(qid_raw))
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
                i = idx.get(key); 
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

# ---------- Admin: manually add a single question ----------
@app.route('/admin/questions/new', methods=['GET','POST'])
@login_required
def admin_new_question():
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    quizzes = Quiz.query.order_by(Quiz.title.asc()).all()
    subjects = Subject.query.order_by(Subject.name.asc()).all()
    if request.method == 'POST':
        quiz_id = request.form.get('quiz_id', type=int)
        subject_name = request.form.get('subject','').strip()
        chapter_name = request.form.get('chapter','').strip()
        text = request.form.get('text','').strip()
        A = request.form.get('A','').strip()
        B = request.form.get('B','').strip()
        C = request.form.get('C','').strip()
        D = request.form.get('D','').strip()
        correct = request.form.get('correct','').strip().upper()[:1]
        if not (quiz_id and text and A and B and C and D and correct in {'A','B','C','D'}):
            flash('Fill all fields correctly','error')
            return render_template('admin_new_question.html', quizzes=quizzes, subjects=subjects)
        subj = get_or_create_subject(subject_name) if subject_name else None
        chap = get_or_create_chapter(subj, chapter_name) if (subj and chapter_name) else None
        q = Question(quiz_id=quiz_id, subject_id=subj.id if subj else None, chapter_id=chap.id if chap else None,
                     text=text, option_a=A, option_b=B, option_c=C, option_d=D, correct_option=correct)
        db.session.add(q); db.session.commit()
        flash('Question added','success')
        return redirect(url_for('admin_new_question'))
    return render_template('admin_new_question.html', quizzes=quizzes, subjects=subjects)

# ----- Subject/Topic explorer for admin -----
@app.route('/admin/subjects')
@login_required
def admin_subjects():
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    subjects = Subject.query.order_by(Subject.name.asc()).all()
    return render_template('admin_subjects.html', subjects=subjects)

@app.route('/admin/subject/<int:subject_id>', methods=['GET','POST'])
@login_required
def admin_subject_detail(subject_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    subject = db.session.get(Subject, subject_id)
    if not subject:
        flash('Subject not found','error'); return redirect(url_for('admin_subjects'))
    # Add topic (chapter)
    if request.method == 'POST':
        chap_name = request.form.get('chapter','').strip()
        if chap_name:
            get_or_create_chapter(subject, chap_name)
            flash('Topic added','success')
            return redirect(url_for('admin_subject_detail', subject_id=subject_id))
    chapters = Chapter.query.filter_by(subject_id=subject.id).order_by(Chapter.name.asc()).all()
    # Filter questions by chapter
    chapter_id = request.args.get('chapter_id', type=int)
    q = Question.query.filter_by(subject_id=subject.id)
    if chapter_id:
        q = q.filter_by(chapter_id=chapter_id)
    questions = q.order_by(Question.id.desc()).all()
    return render_template('admin_subject_detail.html', subject=subject, chapters=chapters, questions=questions, chapter_id=chapter_id)

# ----- Question bank (existing filters) -----
@app.route('/admin/questions')
@login_required
def admin_questions():
    if current_user.role != 'admin':
        return redirect(url_for('index'))
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
    items = q.order_by(Question.id.desc()).limit(1000).all()
    total = q.count()
    return render_template('admin_questions.html', items=items, subjects=subjects, subject_id=subject_id, chapters=chapters, chapter_id=chapter_id, total=total)

# ----- Export attempts CSV -----
@app.route('/admin/export_attempts')
@login_required
def export_attempts():
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['attempt_id','user','quiz','score','started_at','submitted_at'])
    for a in Attempt.query.order_by(Attempt.started_at.desc()).all():
        writer.writerow([a.id, a.user.username, a.quiz.title, a.score, a.started_at, a.submitted_at])
    buf.seek(0)
    return send_file(io.BytesIO(buf.getvalue().encode('utf-8')), mimetype='text/csv', as_attachment=True, download_name='attempts.csv')

# ---------- Start quiz ----------
@app.route('/quiz/<int:quiz_id>/start')
@login_required
def start_quiz(quiz_id):
    if current_user.role != 'admin':
        if not Assignment.query.filter_by(user_id=current_user.id, quiz_id=quiz_id).first():
            flash('Quiz not assigned','error'); return redirect(url_for('index'))
    quiz = db.session.get(Quiz, quiz_id)
    if not quiz:
        flash('Quiz not found','error'); return redirect(url_for('index'))
    at = Attempt(user_id=current_user.id, quiz_id=quiz.id, started_at=datetime.utcnow())
    db.session.add(at); db.session.commit()
    questions = Question.query.filter_by(quiz_id=quiz.id).all()
    random.shuffle(questions)
    deadline = datetime.utcnow() + timedelta(minutes=quiz.duration_minutes)
    deadline_iso = deadline.isoformat() + 'Z'
    return render_template('take_quiz.html', quiz=quiz, questions=questions, deadline_iso=deadline_iso, attempt_id=at.id)

# ---------- Submit quiz ----------
@app.route('/quiz/<int:quiz_id>/submit', methods=['POST'])
@login_required
def submit_quiz(quiz_id):
    quiz = db.session.get(Quiz, quiz_id)
    attempt_id = request.form.get('attempt_id', type=int)
    at = db.session.get(Attempt, attempt_id) if attempt_id else None
    if not at or at.user_id != current_user.id or at.submitted_at:
        at = Attempt.query.filter_by(user_id=current_user.id, quiz_id=quiz.id, submitted_at=None).order_by(Attempt.started_at.desc()).first()
        if not at:
            at = Attempt(user_id=current_user.id, quiz_id=quiz.id, started_at=datetime.utcnow())
            db.session.add(at); db.session.commit()
    questions = Question.query.filter_by(quiz_id=quiz.id).all()
    score = 0.0
    for q in questions:
        sel = request.form.get(f'q_{q.id}', None)
        is_correct = sel is not None and sel.upper()[:1] == q.correct_option
        marks = quiz.marks_per_question if is_correct else (-quiz.negative_marking if sel is not None else 0.0)
        score += marks
        ans = AttemptAnswer(attempt_id=at.id, question_id=q.id, selected_option=(sel.upper()[:1] if sel else None), is_correct=is_correct, marks_earned=marks)
        db.session.add(ans)
    at.score = score
    at.submitted_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('review_attempt', attempt_id=at.id))

# ---------- Review page ----------
@app.route('/attempt/<int:attempt_id>/review')
@login_required
def review_attempt(attempt_id):
    at = db.session.get(Attempt, attempt_id)
    if not at:
        flash('Attempt not found','error'); return redirect(url_for('index'))
    if current_user.role != 'admin' and at.user_id != current_user.id:
        flash('Unauthorized','error'); return redirect(url_for('index'))
    answers = AttemptAnswer.query.filter_by(attempt_id=at.id).all()
    per_subject, per_chapter = {}, {}
    items = []
    for a in answers:
        q = db.session.get(Question, a.question_id)
        subj = db.session.get(Subject, q.subject_id).name if q.subject_id else 'General'
        chap = db.session.get(Chapter, q.chapter_id).name if q.chapter_id else '-'
        ps = per_subject.setdefault(subj, {"total":0,"correct":0,"wrong":0,"marks":0.0})
        pc = per_chapter.setdefault((subj, chap), {"total":0,"correct":0,"wrong":0,"marks":0.0})
        ps["total"] += 1; pc["total"] += 1
        if a.is_correct:
            ps["correct"] += 1; pc["correct"] += 1
        else:
            if a.selected_option is not None:
                ps["wrong"] += 1; pc["wrong"] += 1
        ps["marks"] += a.marks_earned; pc["marks"] += a.marks_earned
        items.append({
            "text": q.text, "A": q.option_a, "B": q.option_b, "C": q.option_c, "D": q.option_d,
            "correct": q.correct_option, "selected": a.selected_option, "marks": a.marks_earned,
            "subject": subj, "chapter": chap
        })
    return render_template('review.html', attempt=at, items=items, per_subject=per_subject, per_chapter=per_chapter)

# ---------- Result page ----------
@app.route('/result/<int:attempt_id>')
@login_required
def show_result(attempt_id):
    at = db.session.get(Attempt, attempt_id)
    if not at:
        flash('Attempt not found','error'); return redirect(url_for('index'))
    if current_user.role != 'admin' and at.user_id != current_user.id:
        flash('Unauthorized','error'); return redirect(url_for('index'))
    return render_template('result.html', attempt=at)

if __name__ == '__main__':
    app.run(debug=True)
