import json
import sqlite3
from contextlib import contextmanager
from config import DATABASE

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, email TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), student_code TEXT UNIQUE, profile TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT, language TEXT DEFAULT 'Python', rubric TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS test_cases(id INTEGER PRIMARY KEY, assignment_id INTEGER REFERENCES assignments(id), label TEXT, visibility TEXT, input_data TEXT, expected TEXT);
CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY, student_id INTEGER REFERENCES students(id), assignment_id INTEGER REFERENCES assignments(id), filename TEXT, source TEXT, submitted_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS evaluations(id INTEGER PRIMARY KEY, submission_id INTEGER UNIQUE REFERENCES submissions(id), status TEXT, proposed REAL, final REAL, passed INTEGER DEFAULT 0, total INTEGER DEFAULT 0, details TEXT, feedback TEXT, faculty_feedback TEXT DEFAULT '', approved_by INTEGER REFERENCES users(id), approved_at TEXT);
CREATE TABLE IF NOT EXISTS evaluation_events(id INTEGER PRIMARY KEY, submission_id INTEGER REFERENCES submissions(id), agent TEXT, action TEXT, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS learning_profiles(id INTEGER PRIMARY KEY, student_id INTEGER UNIQUE REFERENCES students(id), history TEXT DEFAULT '{}', updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS similarity_flags(id INTEGER PRIMARY KEY, submission_a INTEGER REFERENCES submissions(id), submission_b INTEGER REFERENCES submissions(id), score REAL, status TEXT DEFAULT 'Review');
"""

@contextmanager
def connect():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATABASE)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def init_db():
    with connect() as con:
        con.executescript(SCHEMA)
        # Safe additive migration; preserve all existing evaluation rows.
        columns={row['name'] for row in con.execute('PRAGMA table_info(evaluations)')}
        if 'faculty_feedback' not in columns:
            con.execute("ALTER TABLE evaluations ADD COLUMN faculty_feedback TEXT DEFAULT ''")
        # Quarantine old fixture-based evaluations. Keep the complete former
        # result in details for audit/review, but never expose it as verified,
        # proposed, passed, or faculty-approved work.
        legacy_rows=con.execute("SELECT submission_id,details,proposed,final,passed,total,status,approved_by,approved_at FROM evaluations WHERE details LIKE '%simulated outcomes%' OR details LIKE '%simulated fixture outcomes%'").fetchall()
        affected_students=set()
        for row in legacy_rows:
            details=json.loads(row['details'] or '{}')
            if details.get('legacy_quarantined'):
                continue
            details['legacy_quarantined']={
                'reason':'Prior evaluation used simulated fixtures without executing student code; faculty must review again.',
                'previous':{'proposed':row['proposed'],'final':row['final'],'passed':row['passed'],'total':row['total'],'status':row['status'],'approved_by':row['approved_by'],'approved_at':row['approved_at']},
            }
            student=con.execute('SELECT student_id FROM submissions WHERE id=?',(row['submission_id'],)).fetchone()
            if student:
                affected_students.add(student['student_id'])
            con.execute("UPDATE evaluations SET status='Needs review',proposed=0,final=NULL,passed=0,total=0,details=?,faculty_feedback=?,approved_by=NULL,approved_at=NULL WHERE submission_id=?",(json.dumps(details),details['legacy_quarantined']['reason'],row['submission_id']))
        # learning_profiles is only a cache; remove summaries that included a
        # quarantined row so future comparisons use approved evaluations only.
        for student_id in affected_students:
            con.execute("UPDATE learning_profiles SET history='{}',updated_at=CURRENT_TIMESTAMP WHERE student_id=?",(student_id,))
