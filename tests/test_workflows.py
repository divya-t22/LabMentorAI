import json
import tempfile
import unittest
from pathlib import Path
import database.db as db
# app.py calls init_db() at import time, so redirect before importing the app.
WORKSPACE_DATABASE=db.DATABASE
TEST_DATABASE_DIRECTORY=tempfile.TemporaryDirectory(prefix='labmentor-tests-')
db.DATABASE=Path(TEST_DATABASE_DIRECTORY.name)/'test.db'
from database.db import connect
import app as application

class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application.app.config['TESTING'] = True
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        db.DATABASE=WORKSPACE_DATABASE
        TEST_DATABASE_DIRECTORY.cleanup()

    def setUp(self):
        with connect() as con:
            for table in ('similarity_flags','learning_profiles','evaluation_events','evaluations','submissions','test_cases','assignments','students','users'):
                con.execute(f'DELETE FROM {table}')
        self.client=application.app.test_client()

    def test_demo_paths_approval_memory_and_student_report(self):
        response=self.client.post('/demo/init',follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with connect() as con:
            rows=con.execute('SELECT s.id,e.proposed,e.details,e.status FROM submissions s JOIN evaluations e ON e.submission_id=s.id ORDER BY s.id').fetchall()
            details=[json.loads(r['details']) for r in rows]
        self.assertEqual(len(rows),5)
        self.assertEqual({d['execution'] for d in details},{'NOT RUN — secure sandbox unavailable'})
        self.assertTrue(all(d['tests']==[] for d in details))
        self.assertTrue(all('NOT RUN' in d['mode'] for d in details))
        self.assertTrue(all('decisions' in d and d['decisions'] for d in details))
        eval_trace=[item for item in details[0]['decisions'] if item['agent']=='Evaluation Agent']
        self.assertEqual({item['action'] for item in eval_trace},{'static_python_ast_validation','assignment_relevance_check','execution_status','rubric_scoring','static_quality_observations'})
        eval_detail=' '.join(item['detail'] for item in eval_trace)
        self.assertIn('Static AST',eval_detail)
        self.assertIn('not run',eval_detail.lower())
        self.assertNotIn('executed the submitted code',eval_detail.lower())
        rendered=self.client.get(f"/submission/{rows[0]['id']}")
        self.assertEqual(rendered.status_code,200)
        self.assertIn(b'Evaluation Agent',rendered.data)
        self.assertIn(b'static python ast validation',rendered.data)
        self.assertIn(b'Evaluation timeline',rendered.data)
        self.assertIn(b'Conditional routing',rendered.data)
        self.assertIn(b'Learning Agent',rendered.data)
        self.assertIn(b'Pending human decision',rendered.data)
        self.assertIn(b'NOT EVALUATED',rendered.data)
        response=self.client.post(f"/approve/{rows[0]['id']}",data={'mark':'8','faculty_feedback':'Reviewed static evidence.'},follow_redirects=True)
        self.assertEqual(response.status_code,200)
        student=self.client.get('/student/S2401')
        self.assertEqual(student.status_code,200)
        self.assertIn(b'Approved laboratory work',student.data)
        with connect() as con:
            profile=con.execute('SELECT history FROM learning_profiles WHERE student_id=1').fetchone()
            self.assertIsNotNone(profile)
            history=json.loads(profile['history'])
            self.assertEqual(history['labs'],1)
            approved=con.execute('SELECT final,faculty_feedback,approved_by FROM evaluations WHERE submission_id=?',(rows[0]['id'],)).fetchone()
            self.assertEqual((approved['final'],approved['faculty_feedback']),(8,'Reviewed static evidence.'))
            self.assertEqual(con.execute('SELECT role FROM users WHERE id=?',(approved['approved_by'],)).fetchone()['role'],'faculty')
        self.assertEqual(self.client.get('/reports.csv').status_code,200)

    def test_langgraph_persistence_is_idempotent_and_does_not_overwrite_approval(self):
        self.client.post('/demo/init')
        with connect() as con:
            sid=con.execute('SELECT id FROM submissions ORDER BY id LIMIT 1').fetchone()['id']
        approved=self.client.post(f'/approve/{sid}',data={'mark':'7','faculty_feedback':'Human review complete.'})
        self.assertEqual(approved.status_code,302)
        with connect() as con:
            before=con.execute('SELECT status,final,faculty_feedback,details,feedback FROM evaluations WHERE submission_id=?',(sid,)).fetchone()
            event_count=con.execute('SELECT count(*) FROM evaluation_events WHERE submission_id=?',(sid,)).fetchone()[0]
        application.evaluate_submission(sid)
        application.evaluate_submission(sid)
        with connect() as con:
            after=con.execute('SELECT status,final,faculty_feedback,details,feedback FROM evaluations WHERE submission_id=?',(sid,)).fetchone()
            after_event_count=con.execute('SELECT count(*) FROM evaluation_events WHERE submission_id=?',(sid,)).fetchone()[0]
            evaluation_count=con.execute('SELECT count(*) FROM evaluations WHERE submission_id=?',(sid,)).fetchone()[0]
        self.assertEqual(tuple(after),tuple(before))
        self.assertEqual(after_event_count,event_count)
        self.assertEqual(evaluation_count,1)

    def test_learning_agent_first_time_returning_and_personalized_paths(self):
        self.client.post('/demo/init')
        with connect() as con:
            rows=con.execute('SELECT s.id,s.student_id,s.assignment_id,u.name,e.details,e.feedback,e.proposed FROM submissions s JOIN students st ON st.id=s.student_id JOIN users u ON u.id=st.user_id JOIN evaluations e ON e.submission_id=s.id ORDER BY s.student_id').fetchall()
            by_name={r['name']:r for r in rows}
        aarav=by_name['Aarav Patel']; sofia=by_name['Sofia Garcia']
        aarav_feedback=json.loads(aarav['feedback']); sofia_feedback=json.loads(sofia['feedback'])
        self.assertEqual(aarav_feedback['learner_status'],'First-time student (no prior approved labs found)')
        self.assertIn('small testable steps',aarav_feedback['personalization'])
        self.assertIn('small testable steps',sofia_feedback['personalization'])
        trace_agents={item['agent'] for item in json.loads(sofia['details'])['decisions']}
        self.assertEqual(trace_agents,{'Supervisor','Evaluation Agent','Learning Agent'})
        learning_view=self.client.get(f"/submission/{sofia['id']}")
        self.assertIn(b'AGENT EXECUTION TRACE',learning_view.data)
        self.assertIn(b'Learning Agent',learning_view.data)
        self.assertIn(b'First-time student',learning_view.data)

        # Approve the low result; a second evaluation must retrieve that real SQLite history.
        self.client.post(f"/approve/{sofia['id']}",data={'mark':'0'})
        response=self.client.post('/submissions',data={
            'student_code':'S2404','assignment_id':str(sofia['assignment_id']),
            'source':'def even_sum(values):\n    return sum(x for x in values if x % 2 == 0)\n'
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with connect() as con:
            latest=con.execute('SELECT s.id,e.details,e.feedback FROM submissions s JOIN evaluations e ON e.submission_id=s.id WHERE s.student_id=? ORDER BY s.id DESC LIMIT 1',(sofia['student_id'],)).fetchone()
            profile=json.loads(con.execute('SELECT history FROM learning_profiles WHERE student_id=?',(sofia['student_id'],)).fetchone()['history'])
        returning_feedback=json.loads(latest['feedback']); returning_details=json.loads(latest['details'])
        self.assertEqual(returning_feedback['learner_status'],'Returning student')
        self.assertEqual(returning_feedback['previous_mark'],0)
        self.assertTrue(returning_feedback['historical_marks'])
        # A higher simulated score does not resolve an observed runtime error.
        self.assertEqual(returning_feedback['improvement_status'],'not_established')
        self.assertEqual(returning_feedback['improved_concepts'],[])
        self.assertTrue(any('0' in item['detail'] for item in returning_details['decisions'] if item['agent']=='Learning Agent'))
        self.assertEqual(len(profile['records']),1)
        student=self.client.get('/student/S2404')
        self.assertIn(b'Student learning history',student.data)
        self.assertIn(b'Previous lab marks',student.data)
        self.assertIn(b'Current lab',student.data)

    def test_evaluation_approval_memory_regression_across_requests(self):
        # This class is pointed at a temporary SQLite file in setUpClass.
        self.client.post('/demo/init')
        with connect() as con:
            student=con.execute('SELECT id FROM students WHERE student_code=?',('S2405',)).fetchone()
            first=con.execute('SELECT s.id,e.status,e.feedback FROM submissions s JOIN evaluations e ON e.submission_id=s.id WHERE s.student_id=?',(student['id'],)).fetchone()
            self.assertEqual(first['status'],'Proposed')
            self.assertIsNone(con.execute('SELECT 1 FROM learning_profiles WHERE student_id=?',(student['id'],)).fetchone())
            self.assertEqual(json.loads(first['feedback'])['learner_status'],'First-time student (no prior approved labs found)')
            assignment_id=con.execute('SELECT assignment_id FROM submissions WHERE id=?',(first['id'],)).fetchone()['assignment_id']

        # A second pending evaluation before approval must not count as memory.
        response=self.client.post('/submissions',data={
            'student_code':'S2405','assignment_id':str(assignment_id),
            'source':'def even_sum(values):\n    return sum(values)\n'
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with connect() as con:
            pending=con.execute('SELECT e.status,e.feedback FROM submissions s JOIN evaluations e ON e.submission_id=s.id WHERE s.student_id=? ORDER BY s.id DESC LIMIT 1',(student['id'],)).fetchone()
        self.assertEqual(pending['status'],'Proposed')
        self.assertEqual(json.loads(pending['feedback'])['learner_status'],'First-time student (no prior approved labs found)')

        # Approval and profile refresh happen atomically; the next HTTP request sees it.
        response=self.client.post(f"/approve/{first['id']}",data={'mark':'0'},follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with connect() as con:
            approved=con.execute('SELECT status,final,approved_at FROM evaluations WHERE submission_id=?',(first['id'],)).fetchone()
            profile=con.execute('SELECT student_id,history FROM learning_profiles WHERE student_id=?',(student['id'],)).fetchone()
            self.assertEqual((approved['status'],approved['final']),('Approved',0))
            self.assertIsNotNone(approved['approved_at'])
            self.assertEqual(profile['student_id'],student['id'])
            history=json.loads(profile['history'])
            self.assertEqual(history['marks'],[0])
            self.assertEqual([record['submission_id'] for record in history['records']],[first['id']])

        # A later request generates another evaluation. Only the first approved
        # evaluation is retrieved; the still-pending second submission is excluded.
        self.client.post('/submissions',data={
            'student_code':'S2405','assignment_id':str(assignment_id),
            'source':'def even_sum(values):\n    return sum(x for x in values if x % 2 == 0)\n'
        },follow_redirects=True)
        with connect() as con:
            latest=con.execute('SELECT s.id,e.status,e.feedback,e.details FROM submissions s JOIN evaluations e ON e.submission_id=s.id WHERE s.student_id=? ORDER BY s.id DESC LIMIT 1',(student['id'],)).fetchone()
            approved_ids=[r['submission_id'] for r in con.execute("SELECT e.submission_id FROM evaluations e JOIN submissions s ON s.id=e.submission_id WHERE s.student_id=? AND e.status='Approved' AND e.final IS NOT NULL AND e.approved_at IS NOT NULL ORDER BY e.submission_id",(student['id'],))]
            profile_now=json.loads(con.execute('SELECT history FROM learning_profiles WHERE student_id=?',(student['id'],)).fetchone()['history'])
            database_file=Path(con.execute('PRAGMA database_list').fetchone()['file']).resolve()
            fk_errors=con.execute('PRAGMA foreign_key_check').fetchall()
        feedback=json.loads(latest['feedback']); details=json.loads(latest['details'])
        self.assertEqual(latest['status'],'Proposed')
        self.assertEqual(feedback['learner_status'],'Returning student')
        self.assertEqual(feedback['historical_marks'],[0])
        self.assertEqual(feedback['previous_mark'],0)
        self.assertEqual(feedback['historical_labs'],1)
        self.assertEqual(approved_ids,[first['id']])
        self.assertEqual([record['submission_id'] for record in profile_now['records']],approved_ids)
        self.assertEqual(details['student_id'],student['id'])
        self.assertEqual(details['learning_history_source'],'approved evaluations in SQLite')
        trace=' '.join(entry['detail'] for entry in details['decisions'] if entry['agent']=='Learning Agent')
        self.assertIn('student_id=5',trace)
        self.assertIn('marks: 0',trace)
        self.assertNotIn('No earlier faculty-approved evaluation exists',trace)
        self.assertEqual(database_file,db.DATABASE.resolve())
        self.assertEqual(fk_errors,[])

    def test_non_comparable_returning_submission_shows_marks_and_qualifies_improvement(self):
        self.client.post('/demo/init')
        with connect() as con:
            student=con.execute('SELECT id FROM students WHERE student_code=?',('S2405',)).fetchone()
            previous=con.execute('SELECT id,assignment_id FROM submissions WHERE student_id=?',(student['id'],)).fetchone()
        self.client.post(f"/approve/{previous['id']}",data={'mark':'0'},follow_redirects=True)
        response=self.client.post('/submissions',data={
            'student_code':'S2405','assignment_id':str(previous['assignment_id']),
            'source':'from flask import Flask\napp = Flask(__name__)\n@app.get("/")\ndef home():\n    return "ok"\n'
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with connect() as con:
            current=con.execute('SELECT s.id,e.proposed,e.feedback FROM submissions s JOIN evaluations e ON e.submission_id=s.id WHERE s.student_id=? ORDER BY s.id DESC LIMIT 1',(student['id'],)).fetchone()
        feedback=json.loads(current['feedback'])
        self.assertEqual(current['proposed'],0)
        self.assertEqual(feedback['previous_approved_mark'],0)
        self.assertEqual(feedback['current_proposed_mark'],0)
        self.assertEqual(feedback['previous_topics'],['list aggregation and filtering'])
        self.assertEqual(feedback['current_topics'],['Flask web application'])
        self.assertEqual(feedback['topic_comparability'],'not_comparable')
        self.assertEqual(feedback['improvement_status'],'not_established')
        self.assertEqual(feedback['improved_concepts'],[])
        self.assertTrue(all(item['status']=='not_comparable' for item in feedback['previous_mistake_comparison']))
        self.assertTrue(all(item['status']=='not_comparable' for item in feedback['current_mistake_comparison']))
        # Simulate the saved report produced by an older server process, then verify
        # report rendering reconciles it read-only from approved evaluation rows.
        legacy_feedback=json.dumps({'summary':'legacy MOCK AI report','strengths':[]})
        with connect() as con:
            details=json.loads(con.execute('SELECT details FROM evaluations WHERE submission_id=?',(current['id'],)).fetchone()['details'])
            details['decisions']=[d for d in details.get('decisions',[]) if not (d.get('agent')=='Learning Agent' and d.get('action')=='compare_performance') and d.get('agent')!='Evaluation Agent']
            con.execute('UPDATE evaluations SET feedback=?,details=? WHERE submission_id=?',(legacy_feedback,json.dumps(details),current['id']))
        self.assertEqual(current['id'],6)
        report=self.client.get('/submission/6')
        self.assertEqual(report.status_code,200)
        for visible in (
            b'LEARNING HISTORY &amp; PERFORMANCE COMPARISON',
            b'Previous approved evaluation',b'Current proposed evaluation',
            b'Previous observed topic:',b'list aggregation and filtering',
            b'Current observed topic:',b'Flask web application',
            b'Comparability:</b> <strong>NOT COMPARABLE',
            b'Improvement:</b> <strong>NOT ESTABLISHED',
            b'0 / 10',b'These are different tasks',
            b'mark difference does not establish learning improvement',
            b'static python ast validation recovered',
            b'syntax validation recovered',b'code quality observations recovered',
            b'demo fixture evidence recovered',b'evidence handoff recovered',
        ):
            self.assertIn(visible,report.data)
        self.assertIn(b'Previous approved mark: 0/10; current proposed mark: 0/10',report.data)
        self.assertNotIn(b'No prior approved mark is stored for this student.',report.data)
        with connect() as con:
            self.assertEqual(con.execute('SELECT feedback FROM evaluations WHERE submission_id=?',(current['id'],)).fetchone()['feedback'],legacy_feedback)
            saved_details=json.loads(con.execute('SELECT details FROM evaluations WHERE submission_id=?',(current['id'],)).fetchone()['details'])
            self.assertFalse(any(event.get('agent')=='Evaluation Agent' for event in saved_details['decisions']))

    def test_assignment_relevance_warning_for_obvious_flask_mismatch(self):
        self.client.post('/demo/init')
        with connect() as con:
            assignment_id=con.execute('SELECT id FROM assignments WHERE title=?',('Array Signal Lab',)).fetchone()['id']
        response=self.client.post('/submissions',data={
            'student_code':'S2405','assignment_id':str(assignment_id),
            'source':'from flask import Flask, jsonify\napp = Flask(__name__)\n@app.get("/")\ndef home():\n    return jsonify({"ok": True})\n'
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with connect() as con:
            current=con.execute('SELECT s.id,e.status,e.proposed,e.final FROM submissions s JOIN evaluations e ON e.submission_id=s.id WHERE s.student_id=(SELECT id FROM students WHERE student_code=?) ORDER BY s.id DESC LIMIT 1',('S2405',)).fetchone()
        self.assertEqual(current['id'],6)
        self.assertEqual((current['status'],current['proposed'],current['final']),('Proposed',0,None))
        report=self.client.get('/submission/6')
        self.assertEqual(report.status_code,200)
        for item in (
            b'Potential assignment mismatch',b'faculty review required',
            b'imports or references the flask framework',
            b'no sum operation',b'no even-value identifier',
            b'sums even values from a list',
            b'correctness scoring requires faculty review',
            b'Faculty approval required',b'STATIC REVIEW ONLY',
        ):
            self.assertIn(item,report.data)
        self.assertNotIn(b'Automatically rejected',report.data)
        with connect() as con:
            unchanged=con.execute('SELECT status,proposed,final FROM evaluations WHERE submission_id=?',(current['id'],)).fetchone()
        self.assertEqual((unchanged['status'],unchanged['proposed'],unchanged['final']),('Proposed',0,None))

    def test_assignment_relevance_does_not_warn_for_static_even_sum_solution(self):
        self.client.post('/demo/init')
        with connect() as con:
            assignment_id=con.execute('SELECT id FROM assignments WHERE title=?',('Array Signal Lab',)).fetchone()['id']
        self.client.post('/submissions',data={
            'student_code':'S2405','assignment_id':str(assignment_id),
            'source':'def even_sum(values):\n    return sum(value for value in values if value % 2 == 0)\n'
        },follow_redirects=True)
        with connect() as con:
            current=con.execute('SELECT id FROM submissions WHERE student_id=(SELECT id FROM students WHERE student_code=?) ORDER BY id DESC LIMIT 1',('S2405',)).fetchone()
        report=self.client.get(f"/submission/{current['id']}")
        self.assertEqual(report.status_code,200)
        self.assertNotIn(b'Potential assignment mismatch',report.data)

    def test_static_syntax_branch_and_approval_bound(self):
        self.client.post('/demo/init')
        with connect() as con:
            submission=con.execute('SELECT id FROM submissions WHERE student_id=2').fetchone()
        report=self.client.get(f"/submission/{submission['id']}")
        self.assertEqual(report.status_code,200)
        self.assertIn(b'Syntax error',report.data)
        bad=self.client.post(f"/approve/{submission['id']}",data={'mark':'99'},follow_redirects=True)
        self.assertEqual(bad.status_code,200)
        with connect() as con:
            row=con.execute('SELECT status FROM evaluations WHERE submission_id=?',(submission['id'],)).fetchone()
            self.assertEqual(row['status'],'Proposed')

    def test_unrelated_valid_python_gets_no_unverified_credit_and_no_test_claims(self):
        self.client.post('/demo/init')
        with connect() as con:
            assignment_id=con.execute('SELECT id FROM assignments LIMIT 1').fetchone()['id']
        self.client.post('/submissions',data={
            'student_code':'S2405','assignment_id':str(assignment_id),
            'source':'from flask import Flask\napp=Flask(__name__)\n@app.get("/")\ndef home(): return "hello"\n'
        })
        with connect() as con:
            row=con.execute('SELECT e.proposed,e.passed,e.total,e.details FROM evaluations e JOIN submissions s ON s.id=e.submission_id ORDER BY s.id DESC LIMIT 1').fetchone()
        details=json.loads(row['details'])
        self.assertEqual(row['proposed'],0)
        self.assertEqual((row['passed'],row['total']),(0,0))
        self.assertEqual(details['execution'],'NOT RUN — secure sandbox unavailable')
        self.assertEqual(details['tests'],[])
        self.assertTrue(all(item['status']=='NOT EVALUATED' for item in details['rubric']))
        self.assertTrue(details['relevance']['requires_review'])

    def test_rejection_and_faculty_feedback_are_persisted(self):
        self.client.post('/demo/init')
        with connect() as con:
            sid=con.execute('SELECT id FROM submissions LIMIT 1').fetchone()['id']
        response=self.client.post(f'/reject/{sid}',data={'faculty_feedback':'Please resubmit with the required function.'},follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with connect() as con:
            row=con.execute('SELECT status,final,faculty_feedback FROM evaluations WHERE submission_id=?',(sid,)).fetchone()
        self.assertEqual((row['status'],row['final'],row['faculty_feedback']),('Rejected',None,'Please resubmit with the required function.'))

    def test_legacy_simulated_approval_is_quarantined_with_snapshot(self):
        with connect() as con:
            user=con.execute("INSERT INTO users(name,role) VALUES('Legacy Learner','student')").lastrowid
            student=con.execute('INSERT INTO students(user_id,student_code) VALUES(?,?)',(user,'LEGACY1')).lastrowid
            assignment=con.execute("INSERT INTO assignments(title,description,rubric) VALUES('Even Sum','Sum even values in a list.','{\"Correctness\":10}')").lastrowid
            submission=con.execute("INSERT INTO submissions(student_id,assignment_id,filename,source) VALUES(?,?,?,?)",(student,assignment,'old.py','def wrong(): return 1')).lastrowid
            con.execute("INSERT INTO evaluations(submission_id,status,proposed,final,passed,total,details,approved_by,approved_at) VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",(submission,'Approved',10,10,3,3,json.dumps({'mode':'DEMO EVALUATION MODE — simulated outcomes; student code was not executed.'}),1))
            con.execute("INSERT INTO learning_profiles(student_id,history) VALUES(?,?)",(student,json.dumps({'marks':[10],'labs':1})))
        db.init_db()
        with connect() as con:
            row=con.execute('SELECT status,proposed,final,passed,total,details,faculty_feedback FROM evaluations WHERE submission_id=?',(submission,)).fetchone()
            profile=json.loads(con.execute('SELECT history FROM learning_profiles WHERE student_id=?',(student,)).fetchone()['history'])
        details=json.loads(row['details'])
        self.assertEqual((row['status'],row['proposed'],row['final'],row['passed'],row['total']),('Needs review',0,None,0,0))
        self.assertEqual(details['legacy_quarantined']['previous']['final'],10)
        self.assertIn('faculty must review again',row['faculty_feedback'])
        self.assertEqual(profile,{})

    def test_quarantined_ethan_report_separates_history_from_current_review(self):
        self.client.post('/demo/init')
        with connect() as con:
            sid=con.execute("SELECT s.id FROM submissions s JOIN students st ON st.id=s.student_id WHERE st.student_code='S2405' ORDER BY s.id DESC LIMIT 1").fetchone()['id']
            old_details={
                'compilation':'Valid Python syntax','execution':'Demo evaluation complete',
                'tests':[{'label':f'Demo check {i}','passed':True} for i in range(1,4)],
                'errors':[],'quality':["Function 'home' has no docstring.","Function 'api_plan_deliveries' has no docstring."],
                'decisions':[{'agent':'Evaluation Agent','action':'demo_fixture_selection','detail':'3/3 checks passed (simulated).'}],
                'mode':'DEMO EVALUATION MODE — simulated outcomes; student code was not executed.'}
            con.execute("UPDATE submissions SET source=? WHERE id=?",('from flask import Flask\napp=Flask(__name__)\n@app.get("/")\ndef home(): return "ok"\n',sid))
            con.execute("UPDATE evaluations SET status='Approved',proposed=10,final=10,passed=3,total=3,details=?,approved_by=1,approved_at=CURRENT_TIMESTAMP WHERE submission_id=?",(json.dumps(old_details),sid))
        db.init_db()
        response=self.client.get(f'/submission/{sid}')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Historical results are quarantined',response.data)
        self.assertIn(b'Current workflow trace unavailable',response.data)
        self.assertIn(b'Historical quarantine audit record',response.data)
        self.assertIn(b'Historical legacy snapshot only: 3/3 fixture result(s)',response.data)
        self.assertIn(b'0/10 proposed',response.data)
        self.assertIn(b'Final marks pending faculty review.',response.data)
        self.assertIn(b'Assignment-oriented learning guidance',response.data)
        self.assertIn(b'Pending human decision',response.data)
        self.assertIn(b'Not recorded',response.data)
        self.assertIn(b'NOT RUN',response.data)
        self.assertIn(b'NOT EVALUATED',response.data)
        self.assertIn(b'Correctness',response.data)
        self.assertIn(b'quality analysis deferred',response.data)
        self.assertIn(b'DETERMINISTIC FEEDBACK MODE',response.data)
        self.assertNotIn(b'3/3 checks passed (simulated)',response.data)
        self.assertNotIn(b'Function &#39;home&#39; has no docstring',response.data)
        self.assertNotIn(b'Function &#39;api_plan_deliveries&#39; has no docstring',response.data)
        with connect() as con:
            saved=json.loads(con.execute('SELECT details FROM evaluations WHERE submission_id=?',(sid,)).fetchone()['details'])
        self.assertEqual(len(saved['tests']),3)
        self.assertEqual(saved['legacy_quarantined']['previous']['final'],10)

    def test_create_assignment_and_upload_submission(self):
        self.client.post('/demo/init')
        response=self.client.post('/assignments',data={
            'title':'Second Lab','description':'Test a simple function.',
            'criterion':['Correctness','Clarity'],'marks':['7','3'],
            'case_label':['Hidden edge'],'case_visibility':['hidden'],
            'case_input':['secret input'],'case_expected':['secret output']
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with connect() as con:
            aid=con.execute("SELECT id FROM assignments WHERE title='Second Lab'").fetchone()['id']
        response=self.client.post('/submissions',data={'student_code':'S2401','assignment_id':str(aid),'source':'def solve(x):\n    return x\n'},follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with connect() as con:
            result=con.execute('SELECT e.status,e.proposed,e.details FROM evaluations e JOIN submissions s ON s.id=e.submission_id WHERE s.assignment_id=?',(aid,)).fetchone()
        self.assertEqual(result['status'],'Proposed')
        self.assertEqual(result['proposed'],0)
        self.assertNotIn('secret input',result['details'])
        self.assertNotIn('secret output',result['details'])

    def test_landing_portal_navigation_preserves_existing_dashboards(self):
        landing=self.client.get('/')
        self.assertEqual(landing.status_code,200)
        self.assertIn(b'Agentic AI-powered Programming Lab Evaluation and Personalized Learning Assistant.',landing.data)
        self.assertIn(b'Faculty Portal',landing.data)
        self.assertIn(b'Student Portal',landing.data)
        self.assertIn(b'no authentication or role-based authorization',landing.data)
        faculty=self.client.get('/faculty')
        self.assertEqual(faculty.status_code,200)
        self.assertIn(b'FACULTY WORKSPACE',faculty.data)

        self.client.post('/demo/init')
        with connect() as con:
            sid=con.execute("SELECT s.id FROM submissions s JOIN students st ON st.id=s.student_id WHERE st.student_code='S2401' ORDER BY s.id LIMIT 1").fetchone()['id']
        self.client.post(f'/approve/{sid}',data={'mark':'0'})
        redirected=self.client.post('/student-portal',data={'student_code':'S2401'})
        self.assertEqual(redirected.status_code,302)
        self.assertTrue(redirected.headers['Location'].endswith('/student/S2401'))
        student=self.client.get(redirected.headers['Location'])
        self.assertEqual(student.status_code,200)
        self.assertIn(b'Approved laboratory work',student.data)

        invalid=self.client.post('/student-portal',data={'student_code':'NOT-A-STUDENT'},follow_redirects=True)
        self.assertEqual(invalid.status_code,200)
        self.assertIn(b'Enter a student code already in this local demo workspace.',invalid.data)

if __name__=='__main__': unittest.main()
