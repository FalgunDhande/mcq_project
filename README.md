# MCQ Quiz App — Subjects & Chapters

Features:
- Admin/User roles (default admin: admin/admin123)
- Create users & quizzes (duration, negative marking per quiz)
- Upload questions (CSV/XLSX/PDF text). Optional `subject, chapter` columns
- Assign quizzes to users
- Per-attempt randomization of question order
- User review page after submission shows all MCQs with selected vs correct
- Admin Question Bank: filter questions by Subject & Chapter, search text
- Export attempts to CSV (Admin)
- Creative Admin Dashboard with quick stats

Run:
```
python -m venv venv
# Win: venv\Scripts\activate | Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
python app.py
```
