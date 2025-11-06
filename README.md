# MCQ Quiz — Advanced (Subjects, Chapters, Marks)

Features
- Admin/User roles (default admin: admin / admin123)
- Create users & quizzes (duration, marks per question, negative marking)
- Upload questions via CSV/XLSX/PDF (optional columns: subject, chapter)
- Assign quizzes to users
- Timed quiz with countdown, randomized question order
- User review page after submission: shows all MCQs, chosen vs correct, marks
- Subject-wise & Chapter-wise summaries (counts + marks)
- Admin Question Bank with filters
- Export attempts to CSV (no heavy deps)
- Render-ready: `render.yaml`, `Procfile`, Postgres via `DATABASE_URL`

Run locally
```
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Deploy on Render
- Push this folder to a GitHub repo
- Render → New → Blueprint → pick repo → Deploy
