import csv, io, json, os
from flask import Flask, render_template, request, redirect, url_for, flash, Response, abort
from config import SECRET_KEY, UPLOAD_DIR
from database.db import connect, init_db
from agents.state import EvaluationState
from agents.supervisor import run as supervise
from agents.graph import evaluation_graph
from agents.graph_state import make_initial_state
from tools.code_analysis import analyze
from tools.similarity import compare
from tools.learning_comparison import evidence_topics, compare_history
from tools.assignment_relevance import assess_relevance

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024
UPLOAD_DIR.mkdir(exist_ok=True)

def load_json(value, fallback):
    try: return json.loads(value)
    except (TypeError, ValueError): return fallback

def log_event(con, sid, agent, action, detail):
    con.execute('INSERT INTO evaluation_events(submission_id,agent,action,detail) VALUES(?,?,?,?)',(sid,agent,action,detail))

def restore_evaluation_trace(details):
    """Recover a visible trace for legacy reports from persisted evaluation evidence.

    Current evaluations save explicit Evaluation Agent events. Older server
    processes saved the results but omitted those events, so report rendering
    labels the evidence as recovered and never writes it back to SQLite.
    """
    decisions=details.get('decisions')
    if not isinstance(decisions,list) or any(item.get('agent')=='Evaluation Agent' for item in decisions):
        return
    recovered=[]
    if 'compilation' in details:
        recovered.append({'agent':'Evaluation Agent','action':'static_python_ast_validation_recovered','detail':f"Recovered from persisted evaluation fields. Recorded syntax status: {details.get('compilation')}; {len(details.get('quality',[]))} static code-quality observation(s) were saved."})
        recovered.append({'agent':'Evaluation Agent','action':'syntax_validation_recovered','detail':f"Persisted syntax-validation result: {details.get('compilation')}."})
    if 'quality' in details:
        quality=details.get('quality') or []
        recovered.append({'agent':'Evaluation Agent','action':'code_quality_observations_recovered','detail':'; '.join(quality) if quality else 'Persisted static analysis contains no code-quality observations.'})
    if 'tests' in details or 'execution' in details:
        tests=details.get('tests') or []
        demo_tests=[test for test in tests if str(test.get('label','')).lower().startswith('demo check')]
        if demo_tests:
            passed=sum(1 for test in demo_tests if test.get('passed'))
            fixture_detail=f"Persisted demo fixture outcomes: {passed}/{len(demo_tests)} checks passed (simulated); source code was not executed."
        else:
            fixture_detail=f"No demo fixture results were stored. Recorded evaluation status: {details.get('execution','unavailable')}; source code was not executed."
        recovered.append({'agent':'Evaluation Agent','action':'demo_fixture_evidence_recovered','detail':fixture_detail})
    supervisor_actions=[item.get('action') for item in decisions if item.get('agent')=='Supervisor']
    if supervisor_actions:
        recovered.append({'agent':'Evaluation Agent','action':'evidence_handoff_recovered','detail':f"Supervisor trace records subsequent route(s): {', '.join(action for action in supervisor_actions if action)}. This report is recovering the handoff from the persisted shared evaluation result; no execution is implied."})
    decisions.extend(recovered)

def approved_learning_history(con, student_id, exclude_submission_id=None):
    """Build memory only from final, faculty-approved evaluation rows.

    learning_profiles is a materialized summary; evaluations is the approval source
    of truth so stale summaries can never turn a pending mark into history.
    """
    rows=con.execute("""
        SELECT e.submission_id,e.final,e.passed,e.total,e.details,e.feedback,a.title,a.description,s.source,e.approved_at
        FROM evaluations e
        JOIN submissions s ON s.id=e.submission_id
        JOIN assignments a ON a.id=s.assignment_id
        WHERE s.student_id=? AND e.status='Approved'
          AND e.final IS NOT NULL AND e.approved_at IS NOT NULL
          AND (? IS NULL OR e.submission_id<>?)
        ORDER BY e.approved_at ASC,e.submission_id ASC
    """,(int(student_id),exclude_submission_id,exclude_submission_id)).fetchall()
    records=[]
    for row in rows:
        details=load_json(row['details'],{})
        feedback=load_json(row['feedback'],{})
        records.append({
            'submission_id':row['submission_id'],'assignment':row['title'],'mark':row['final'],
            'demo_passed':row['passed'] or 0,'demo_total':row['total'] or 0,
            'mistakes':list(dict.fromkeys(details.get('errors',[])+details.get('quality',[]))),
            'comparison_topics':evidence_topics(row['title'],row['description'],row['source']),
            'weak_concepts':feedback.get('current_weak_concepts',[]),
            'strengths':feedback.get('strengths',[]),
            'improved_concepts':feedback.get('improved_concepts',[]),
            'recommended_topics':feedback.get('concepts',[]),
        })
    return {
        'labs':len(records),
        'marks':[record['mark'] for record in records],
        'records':records,
        'mistakes':list(dict.fromkeys(mistake for record in records for mistake in record['mistakes'])),
        'weak_concepts':list(dict.fromkeys(concept for record in records for concept in record['weak_concepts'])),
        'strengths':list(dict.fromkeys(strength for record in records for strength in record['strengths'])),
        'topics':list(dict.fromkeys(topic for record in records for topic in record['recommended_topics'])),
    }

def _collect_graph_input(submission_id):
    """Read database context briefly; never carry a connection into the graph."""
    with connect() as con:
        row=con.execute('''
            SELECT s.id submission_id,s.student_id,s.assignment_id,s.source,
                   u.name student_name,a.title assignment_title,a.description assignment_description,a.rubric
            FROM submissions s
            JOIN students st ON st.id=s.student_id
            JOIN users u ON u.id=st.user_id
            JOIN assignments a ON a.id=s.assignment_id
            WHERE s.id=?
        ''',(submission_id,)).fetchone()
        if not row:
            raise ValueError(f'Submission {submission_id} does not exist.')
        history=approved_learning_history(con,row['student_id'],exclude_submission_id=submission_id)
        test_count=con.execute('SELECT count(*) FROM test_cases WHERE assignment_id=?',(row['assignment_id'],)).fetchone()[0]
        rubric=load_json(row['rubric'],{})
        topics=evidence_topics(row['assignment_title'],row['assignment_description'],row['source'])
        return make_initial_state(
            submission_id=row['submission_id'],
            student_id=row['student_id'],
            student_name=row['student_name'],
            assignment_id=row['assignment_id'],
            assignment_title=row['assignment_title'],
            assignment_description=row['assignment_description'] or '',
            assignment_rubric=rubric,
            source_code=row['source'] or '',
            configured_test_case_count=test_count,
            approved_learning_history=history,
            evidence_topics=topics,
        )


def _fallback_graph_result(initial, exc):
    """Last-resort report if LangGraph itself fails outside a handled node."""
    reason=f'Graph execution failed safely ({type(exc).__name__}); faculty review is required.'
    criteria=[{'criterion':name,'maximum':points,'awarded':None,'status':'NOT EVALUATED','evidence_type':'unavailable','evidence':'Graph execution did not produce verifiable criterion evidence.'} for name,points in initial['assignment_rubric'].items()]
    decisions=list(initial.get('decisions',[]))+[{'agent':'Supervisor','action':'safe_graph_failure','detail':reason}]
    return {
        **initial,
        'compilation_status':'Not checked',
        'execution_status':'NOT RUN — secure sandbox unavailable',
        'test_results':[],
        'quality_observations':[],
        'evaluation_errors':[reason],
        'evidence':{'verified':[],'static_observations':[],'not_evaluated':['Program execution','Configured test cases','Program logic and correctness']+[item['criterion'] for item in criteria]},
        'rubric_breakdown':criteria,
        'proposed_marks':0.0,
        'route_decision':'workflow_error',
        'route_reason':reason,
        'decisions':decisions,
        'learning_feedback':{'summary':'DETERMINISTIC FEEDBACK MODE: graph execution failed; no correctness evidence is available. No LLM was called.'},
        'error_info':{'node':'graph','type':type(exc).__name__,'message':str(exc)[:500]},
        'evaluation_status':'Needs review',
        'faculty_review_required':True,
        'faculty_review_status':'Pending',
        'final_report':{'proposed_marks':0.0,'evaluation_status':'Needs review','faculty_review_status':'Pending'},
    }


def evaluate_submission(submission_id):
    """Run LangGraph outside SQLite transactions, then persist once atomically."""
    initial=_collect_graph_input(int(submission_id))
    try:
        result=evaluation_graph.invoke(initial)
    except Exception as exc:
        result=_fallback_graph_result(initial,exc)

    rubric_breakdown=result.get('rubric_breakdown',[])
    evidence=result.get('evidence',{})
    decisions=result.get('decisions',[])
    details={
        'compilation':result.get('compilation_status','Not checked'),
        'execution':result.get('execution_status','NOT RUN — secure sandbox unavailable'),
        'tests':result.get('test_results',[]),
        'rubric':rubric_breakdown,
        'relevance':result.get('assignment_relevance',{}),
        'errors':result.get('evaluation_errors',[]),
        'quality':result.get('quality_observations',[]),
        'decisions':decisions,
        'mode':'STATIC REVIEW ONLY — LangGraph workflow; execution and tests NOT RUN; student code was not executed.',
        'evidence':evidence,
        'graph':{
            'route_decision':result.get('route_decision'),
            'route_reason':result.get('route_reason'),
            'evaluation_status':result.get('evaluation_status'),
            'faculty_review_required':result.get('faculty_review_required',True),
            'faculty_review_status':'Pending',
            'error_info':result.get('error_info'),
        },
        'student_id':initial['student_id'],
        'learning_history_source':'approved evaluations in SQLite',
    }
    feedback=result.get('learning_feedback') or {
        'summary':'DETERMINISTIC FEEDBACK MODE: assignment relevance or missing evidence requires faculty review. No LLM was called.'
    }
    tests=result.get('test_results',[])
    passed=sum(1 for test in tests if test.get('verified') is True and test.get('source')=='sandbox' and test.get('passed'))
    total=len([test for test in tests if test.get('verified') is True and test.get('source')=='sandbox'])

    with connect() as con:
        # Insert-only makes retries/replays idempotent and never overwrites an
        # existing approval, rejection, report, or quarantine snapshot.
        inserted=con.execute('''
            INSERT INTO evaluations(submission_id,status,proposed,passed,total,details,feedback)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(submission_id) DO NOTHING
        ''',(submission_id,'Proposed',0.0,passed,total,json.dumps(details),json.dumps(feedback))).rowcount
        if not inserted:
            return result
        for event in decisions:
            con.execute('''
                INSERT INTO evaluation_events(submission_id,agent,action,detail)
                SELECT ?,?,?,? WHERE NOT EXISTS (
                    SELECT 1 FROM evaluation_events WHERE submission_id=? AND agent=? AND action=? AND detail=?
                )
            ''',(submission_id,event['agent'],event['action'],event['detail'],submission_id,event['agent'],event['action'],event['detail']))

        # Similarity remains a static review signal. Avoid inserting duplicate
        # pairs if the workflow is retried or invoked twice.
        fingerprint=analyze(initial['source_code'])['fingerprint']
        others=con.execute('SELECT id,source FROM submissions WHERE assignment_id=? AND id<>?',(initial['assignment_id'],submission_id)).fetchall()
        for other in others:
            score=compare(fingerprint,analyze(other['source'])['fingerprint'])
            if score>=82:
                left,right=sorted([submission_id,other['id']])
                con.execute('''
                    INSERT INTO similarity_flags(submission_a,submission_b,score,status)
                    SELECT ?,?,?,'Review' WHERE NOT EXISTS (
                        SELECT 1 FROM similarity_flags WHERE submission_a=? AND submission_b=?
                    )
                ''',(left,right,score,left,right))
    return result

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/faculty')
def dashboard():
    with connect() as con:
        stats=con.execute("SELECT (SELECT count(*) FROM students) students,(SELECT count(*) FROM submissions) submissions,(SELECT count(*) FROM evaluations WHERE status='Approved') approved,(SELECT count(*) FROM submissions s LEFT JOIN evaluations e ON e.submission_id=s.id WHERE e.id IS NULL OR e.status NOT IN ('Approved','Rejected')) pending,(SELECT count(*) FROM similarity_flags WHERE status='Review') review,(SELECT coalesce(avg(final),0) FROM evaluations WHERE status='Approved') average").fetchone()
        assignments=con.execute('SELECT a.*,count(s.id) submissions FROM assignments a LEFT JOIN submissions s ON s.assignment_id=a.id GROUP BY a.id ORDER BY a.id DESC').fetchall()
        rows=con.execute('SELECT s.id submission_id,s.filename,s.submitted_at,u.name student,a.title,e.proposed,e.final,e.status,e.passed,e.total,e.details FROM submissions s JOIN students st ON st.id=s.student_id JOIN users u ON u.id=st.user_id JOIN assignments a ON a.id=s.assignment_id LEFT JOIN evaluations e ON e.submission_id=s.id ORDER BY s.id DESC').fetchall()
        flags=con.execute('SELECT f.score,ua.name left_name,ub.name right_name FROM similarity_flags f JOIN submissions sa ON sa.id=f.submission_a JOIN students sta ON sta.id=sa.student_id JOIN users ua ON ua.id=sta.user_id JOIN submissions sb ON sb.id=f.submission_b JOIN students stb ON stb.id=sb.student_id JOIN users ub ON ub.id=stb.user_id WHERE f.status="Review"').fetchall()
    return render_template('dashboard.html',stats=stats,assignments=assignments,rows=rows,flags=flags)

@app.route('/demo/init',methods=['POST'])
def init_demo():
    submission_ids=[]
    with connect() as con:
        count=con.execute('SELECT count(*) FROM assignments').fetchone()[0]
        if count:
            flash('Demo data already exists. Add more records from the forms below.','info'); return redirect(url_for('dashboard'))
        con.execute("INSERT INTO users(name,role,email) VALUES('Dr. Maya Chen','faculty','faculty@labmentor.local')")
        roster=[('Aarav Patel','S2401'),('Mia Johnson','S2402'),('Noah Kim','S2403'),('Sofia Garcia','S2404'),('Ethan Brown','S2405')]
        for name,code in roster:
            cur=con.execute('INSERT INTO users(name,role,email) VALUES(?,?,?)',(name,'student',code.lower()+'@labmentor.local'))
            con.execute('INSERT INTO students(user_id,student_code) VALUES(?,?)',(cur.lastrowid,code))
        rubric={'Correctness':5,'Logic and approach':2,'Code quality':1,'Edge-case handling':1,'Efficiency':1}
        cur=con.execute('INSERT INTO assignments(title,description,rubric) VALUES(?,?,?)',('Array Signal Lab','Write a Python function that returns the sum of all even values in a list. Include empty-list and boundary handling.',json.dumps(rubric)))
        aid=cur.lastrowid
        for label,vis,inp,exp in [('Basic','public','[2, 3, 4]','6'),('Empty','hidden','[]','0'),('Negative','hidden','[-2, 3, 8]','6')]: con.execute('INSERT INTO test_cases(assignment_id,label,visibility,input_data,expected) VALUES(?,?,?,?,?)',(aid,label,vis,inp,exp))
        samples=['def even_sum(values):\n    return sum(x for x in values if x % 2 == 0)\n','def even_sum(values)\n    return sum(values)\n','from flask import Flask\napp = Flask(__name__)\n','def even_sum(values):\n    return 0\n','def even_sum(values):\n    for value in values:\n        pass\n    return 0\n']
        for idx,src in enumerate(samples,1):
            cur=con.execute('INSERT INTO submissions(student_id,assignment_id,filename,source) VALUES(?,?,?,?)',(idx,aid,f'{roster[idx-1][1]}_array_signal.py',src))
            submission_ids.append(cur.lastrowid)
    for submission_id in submission_ids:
        evaluate_submission(submission_id)
    flash('Sample workspace initialized: one lab, five submissions, rubric and test metadata. All submissions remain unexecuted and require faculty review.','success')
    return redirect(url_for('dashboard'))

@app.route('/assignments',methods=['POST'])
def create_assignment():
    try:
        rubric={k.strip():float(v) for k,v in zip(request.form.getlist('criterion'),request.form.getlist('marks')) if k.strip() and float(v)>=0}
        if not rubric: raise ValueError('Add at least one rubric criterion.')
        with connect() as con:
            cur=con.execute('INSERT INTO assignments(title,description,rubric) VALUES(?,?,?)',(request.form['title'].strip(),request.form.get('description','').strip(),json.dumps(rubric)))
            aid=cur.lastrowid
            for label,vis,inp,expected in zip(request.form.getlist('case_label'),request.form.getlist('case_visibility'),request.form.getlist('case_input'),request.form.getlist('case_expected')):
                if label.strip(): con.execute('INSERT INTO test_cases(assignment_id,label,visibility,input_data,expected) VALUES(?,?,?,?,?)',(aid,label.strip(),vis,inp,expected))
        flash('Assignment created. Upload submissions to evaluate them in demo mode.','success')
    except (ValueError,KeyError) as exc: flash(f'Could not create assignment: {exc}','error')
    return redirect(url_for('dashboard'))

@app.route('/students',methods=['POST'])
def create_student():
    name=request.form.get('name','').strip(); code=request.form.get('code','').strip()
    if not name or not code: flash('Student name and ID are required.','error')
    else:
        try:
            with connect() as con:
                cur=con.execute('INSERT INTO users(name,role,email) VALUES(?,?,?)',(name,'student',code.lower()+'@labmentor.local'))
                con.execute('INSERT INTO students(user_id,student_code) VALUES(?,?)',(cur.lastrowid,code))
            flash(f'Added {name} ({code}).','success')
        except Exception as exc: flash(f'Could not add student: {exc}','error')
    return redirect(url_for('dashboard'))

@app.route('/submissions',methods=['POST'])
def upload_submission():
    file=request.files.get('source_file'); source=request.form.get('source','')
    if file and file.filename:
        if not file.filename.lower().endswith('.py'): flash('Only .py submissions are accepted.','error'); return redirect(url_for('dashboard'))
        source=file.read().decode('utf-8-sig',errors='replace'); filename=os.path.basename(file.filename)
    else: filename='pasted_submission.py'
    try:
        with connect() as con:
            student=con.execute('SELECT id FROM students WHERE student_code=?',(request.form.get('student_code','').strip(),)).fetchone()
            assignment_id=int(request.form['assignment_id'])
            if not student: raise ValueError('Choose a valid student.')
            cur=con.execute('INSERT INTO submissions(student_id,assignment_id,filename,source) VALUES(?,?,?,?)',(student['id'],assignment_id,filename,source))
            submission_id=cur.lastrowid
        evaluate_submission(submission_id)
        flash('Static review recorded. Execution and assignment tests were NOT RUN; faculty review is required.','success')
    except Exception as exc: flash(f'Upload failed: {exc}','error')
    return redirect(url_for('dashboard'))

@app.route('/evaluate-all',methods=['POST'])
def evaluate_all():
    with connect() as con:
        submission_ids=[row['id'] for row in con.execute('SELECT s.id FROM submissions s LEFT JOIN evaluations e ON e.submission_id=s.id WHERE e.id IS NULL').fetchall()]
    for submission_id in submission_ids:
        evaluate_submission(submission_id)
    flash(f'Processed {len(submission_ids)} pending submission(s) in demo mode.','success')
    return redirect(url_for('dashboard'))

@app.route('/submission/<int:sid>')
def submission_detail(sid):
    with connect() as con:
        row=con.execute('SELECT s.*,u.name student,u.id user_id,st.student_code,a.title assignment,a.description assignment_description,a.rubric assignment_rubric,e.* FROM submissions s JOIN students st ON st.id=s.student_id JOIN users u ON u.id=st.user_id JOIN assignments a ON a.id=s.assignment_id LEFT JOIN evaluations e ON e.submission_id=s.id WHERE s.id=?',(sid,)).fetchone()
        if not row: abort(404)
        details=load_json(row['details'],{}); feedback=load_json(row['feedback'],{})
        relevance=assess_relevance(row['assignment_description'],row['source'])
        prior=approved_learning_history(con,row['student_id'],exclude_submission_id=sid)
        legacy_snapshot=details.get('legacy_quarantined')
        if legacy_snapshot:
            # Rebuild the *current* visible report from safe static analysis.
            # The quarantined result remains stored untouched for audit only.
            state=EvaluationState(row['student_id'],row['assignment_id'],sid,row['source'],load_json(row['assignment_rubric'],{}),previous_history=prior)
            state.evidence_topics=evidence_topics(row['assignment'],row['assignment_description'],row['source'])
            cases=con.execute('SELECT id,label,visibility,input_data,expected FROM test_cases WHERE assignment_id=? ORDER BY id',(row['assignment_id'],)).fetchall()
            state=supervise(state,row['student'],row['assignment_description'] or '',cases)
            details={
                'compilation':state.compilation_status,
                'execution':state.execution_status,
                'tests':[],
                'rubric':state.rubric_results,
                'relevance':state.assignment_relevance,
                'errors':state.errors,
                'quality':state.quality_issues,
                'decisions':state.decisions,
                'mode':'CURRENT STATIC REVIEW — execution and tests NOT RUN. Legacy fixture results are shown separately as historical audit data.',
                'evidence':{
                    'verified':[f"Static AST syntax check: {state.compilation_status}. This does not verify behavior."],
                    'static_observations':state.assignment_relevance.get('evidence',[])+state.quality_issues,
                    'not_evaluated':['Program execution','Configured test cases','Program logic and correctness']+[item['criterion'] for item in state.rubric_results],
                },
                'student_id':row['student_id'],
                'legacy_history_source':'Preserved in evaluations.details.legacy_quarantined; excluded from current evidence and learning history.',
            }
            previous=legacy_snapshot.get('previous',{})
            old_passed=previous.get('passed',0) or 0
            old_total=previous.get('total',0) or 0
            if old_total:
                details['decisions'].append({'agent':'Evaluation Agent','action':'historical_legacy_results_quarantined',
                    'detail':f"Historical legacy snapshot only: {old_passed}/{old_total} fixture result(s) were previously recorded as passed. These were simulated, are excluded from current evidence, and do not describe current execution."})
            feedback=state.feedback
        else:
            restore_evaluation_trace(details)
        previous_record=prior.get('records',[])[-1] if prior.get('records') else None
        comparison=compare_history(previous_record,evidence_topics(row['assignment'],row['assignment_description'],row['source']),prior.get('marks',[]),row['proposed'],details.get('errors',[])+details.get('quality',[]),details.get('compilation',''))
        flags=con.execute('SELECT score FROM similarity_flags WHERE submission_a=? OR submission_b=?',(sid,sid)).fetchall()
    feedback.update(comparison)
    # Reports created by older app processes lack the comparison payload. Reconcile
    # their display from the approved evaluation rows without rewriting saved records.
    feedback['learner_status']='Returning student' if prior.get('records') else 'First-time student (no prior approved labs found)'
    feedback['historical_labs']=len(prior.get('records',[]))
    feedback['historical_marks']=prior.get('marks',[])
    if comparison['previous_approved_mark'] is not None:
        mark=comparison['previous_approved_mark']
        current=comparison['current_proposed_mark']
        if current is None:
            feedback['trend']=f'Previous approved mark: {mark:g}/10. No current proposed mark is available.'
        else:
            difference=comparison['mark_difference']
            feedback['trend']=f'Previous approved mark: {mark:g}/10; current proposed mark: {current:g}/10 (difference {difference:+g}). A mark difference alone is not evidence of improvement.'
    else:
        feedback['trend']='No prior approved mark is stored for this student.'
    feedback['previous_weak_concepts']=previous_record.get('weak_concepts',[]) if previous_record else []
    feedback['previous_strengths']=previous_record.get('strengths',[]) if previous_record else []
    if not any(item.get('agent')=='Learning Agent' and item.get('action')=='compare_performance' for item in details.get('decisions',[])):
        detail=f"Read-only report comparison against {len(prior.get('records',[]))} approved SQLite record(s) for student_id={row['student_id']}: {comparison['topic_comparability']}; improvement={comparison['improvement_status']}."
        details.setdefault('decisions',[]).append({'agent':'Learning Agent','action':'compare_performance','detail':detail})
    return render_template('submission.html',row=row,details=details,feedback=feedback,flags=flags,relevance=relevance)

@app.route('/approve/<int:sid>',methods=['POST'])
def approve(sid):
    try:
        mark=float(request.form.get('mark',''))
        with connect() as con:
            row=con.execute('SELECT e.proposed,a.rubric,a.title,s.assignment_id,s.student_id FROM evaluations e JOIN submissions s ON s.id=e.submission_id JOIN assignments a ON a.id=s.assignment_id WHERE e.submission_id=?',(sid,)).fetchone()
            if not row: abort(404)
            maximum=sum(load_json(row['rubric'],{}).values())
            if mark<0 or mark>maximum: raise ValueError(f'Mark must be between 0 and {maximum:g}.')
            approver=con.execute("SELECT id FROM users WHERE role='faculty' ORDER BY id LIMIT 1").fetchone()
            if approver is None:
                raise ValueError('No faculty approver is configured; approval was not recorded.')
            con.execute("UPDATE evaluations SET status='Approved',final=?,faculty_feedback=?,approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE submission_id=? AND status<>'Approved'",(mark,request.form.get('faculty_feedback','').strip(),approver['id'],sid))
            # Rebuild the profile cache from approved rows inside the same transaction.
            # This repairs legacy/stale summaries without admitting proposed evaluations.
            history=approved_learning_history(con,row['student_id'])
            con.execute('INSERT INTO learning_profiles(student_id,history,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(student_id) DO UPDATE SET history=excluded.history,updated_at=CURRENT_TIMESTAMP',(row['student_id'],json.dumps(history)))
        flash('Faculty approval saved; the student view now shows the final mark.','success')
    except ValueError as exc: flash(str(exc),'error')
    return redirect(url_for('submission_detail',sid=sid))

@app.route('/reject/<int:sid>',methods=['POST'])
def reject(sid):
    with connect() as con:
        row=con.execute("SELECT status FROM evaluations WHERE submission_id=?",(sid,)).fetchone()
        if not row: abort(404)
        if row['status']!='Approved':
            con.execute("UPDATE evaluations SET status='Rejected',final=NULL,faculty_feedback=?,approved_by=NULL,approved_at=NULL WHERE submission_id=?",(request.form.get('faculty_feedback','').strip(),sid))
    flash('Submission rejected; proposed marks were not recorded as final.','info')
    return redirect(url_for('submission_detail',sid=sid))

@app.route('/student/<code>')
def student_dashboard(code):
    with connect() as con:
        student=con.execute('SELECT s.*,u.name FROM students s JOIN users u ON u.id=s.user_id WHERE s.student_code=?',(code,)).fetchone()
        if not student: abort(404)
        rows=con.execute("SELECT s.id,s.filename,s.source,a.title,a.description assignment_description,e.status,e.proposed,e.final,e.passed,e.total,e.feedback,e.faculty_feedback,e.details,e.approved_at FROM submissions s JOIN assignments a ON a.id=s.assignment_id LEFT JOIN evaluations e ON e.submission_id=s.id WHERE s.student_id=? AND e.status='Approved' ORDER BY e.approved_at DESC,s.id DESC",(student['id'],)).fetchall()
        profile=con.execute('SELECT history FROM learning_profiles WHERE student_id=?',(student['id'],)).fetchone()
    history=load_json(profile['history'],{}) if profile else {}
    history['approved_records']=[{'assignment':r['title'],'mark':r['final'],'passed':r['passed'],'total':r['total'],'feedback':load_json(r['feedback'],{}),'details':load_json(r['details'],{})} for r in rows]
    history['labs']=len(rows)
    history['marks']=[r['final'] for r in reversed(rows)]
    current_topics=evidence_topics(rows[0]['title'],rows[0]['assignment_description'],rows[0]['source']) if rows else []
    previous_topics=evidence_topics(rows[1]['title'],rows[1]['assignment_description'],rows[1]['source']) if len(rows)>1 else []
    history['previous_comparable']=bool(set(current_topics).intersection(previous_topics))
    history['current_topics']=current_topics
    return render_template('student.html',student=student,rows=rows,history=history)

@app.route('/student-portal',methods=['POST'])
def student_portal():
    code=request.form.get('student_code','').strip()
    with connect() as con:
        exists=con.execute('SELECT 1 FROM students WHERE student_code=?',(code,)).fetchone()
    if not exists:
        flash('Enter a student code already in this local demo workspace.','error')
        return redirect(url_for('landing'))
    return redirect(url_for('student_dashboard',code=code))

@app.route('/reports.csv')
def reports():
    with connect() as con: rows=con.execute('SELECT st.student_code,u.name,a.title,e.status,e.proposed,e.final,e.passed,e.total FROM evaluations e JOIN submissions s ON s.id=e.submission_id JOIN students st ON st.id=s.student_id JOIN users u ON u.id=st.user_id JOIN assignments a ON a.id=s.assignment_id ORDER BY a.id,u.name').fetchall()
    out=io.StringIO(); writer=csv.writer(out); writer.writerow(['Student ID','Student','Assignment','Status','Proposed','Final','Demo checks passed','Demo checks total'])
    for r in rows: writer.writerow(list(r))
    return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=labmentor-report.csv'})

@app.cli.command('init-db')
def init_db_command(): init_db(); print('Database initialized.')

init_db()
if __name__=='__main__': app.run(debug=True)
