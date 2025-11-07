# MCQ Quiz (Render Ready)

- Python: set `PYTHON_VERSION=3.12.6` in Render Environment.
- If `DATABASE_URL` is present (Postgres), app will use it. Otherwise it falls back to local SQLite at `instance/quiz.db`.
- Admin default: `admin / admin123` (auto-created only if not present).

## Running locally
```bash
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python app.py
# http://127.0.0.1:5000
```
