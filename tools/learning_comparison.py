"""Conservative, evidence-based comparison helpers for learning reports."""
import ast
import re


def evidence_topics(assignment_title='', description='', source=''):
    """Return narrow subject tags; recognized source structure outranks lab title."""
    text=(source or '').lower()
    tags=set()
    try:
        tree=ast.parse(source or '')
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == 'flask':
                tags.add('Flask web application')
            elif isinstance(node, ast.Import):
                if any(alias.name == 'flask' for alias in node.names):
                    tags.add('Flask web application')
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.lower() == 'even_sum' or 'sum' in node.name.lower() and 'even' in node.name.lower():
                    tags.add('list aggregation and filtering')
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'Flask':
                tags.add('Flask web application')
    except (SyntaxError, ValueError, TypeError):
        pass
    if 'even_sum' in text or ('% 2' in text and 'sum(' in text):
        tags.add('list aggregation and filtering')
    # When source identifies a concrete topic, don't inherit a possibly stale or
    # generic assignment label (e.g. an app.py uploaded to the even_sum lab).
    if tags:
        return sorted(tags)
    label=f'{assignment_title} {description}'.lower()
    if any(word in label for word in ('array','list','even_sum','aggregation')):
        tags.add('list aggregation and filtering')
    elif any(word in label for word in ('flask','web app','route','api')):
        tags.add('Flask web application')
    else:
        tokens=set(re.findall(r'[a-z][a-z0-9_]{3,}', label))
        tags.update(sorted(tokens)[:5])
    return sorted(tags)


def compare_history(previous_record, current_topics, previous_marks, current_mark,
                    current_mistakes, current_compilation=''):
    """Compare one approved record to current evidence without mark-based inference."""
    current_topics=sorted(set(current_topics or []))
    current_mistakes=list(dict.fromkeys(current_mistakes or []))
    previous_mark=previous_marks[-1] if previous_marks else None
    result={
        'previous_approved_mark':previous_mark,
        'current_proposed_mark':current_mark,
        'previous_marks':list(previous_marks or []),
        'current_topics':current_topics,
        'previous_topics':[],
        'topic_comparability':'no_prior_approved_evaluation',
        'comparability_reason':'There is no previous approved evaluation to compare.',
        'previous_mistake_comparison':[],
        'current_mistake_comparison':[],
        'improvement_status':'not_established',
        'improvement_reason':'There is no prior approved evaluation to establish improvement.',
        'improved_concepts':[],
        'mark_difference':None,
    }
    if previous_mark is not None and current_mark is not None:
        result['mark_difference']=round(float(current_mark)-float(previous_mark),1)
    if not previous_record:
        result['current_mistake_comparison']=[{'mistake':issue,'status':'new','reason':'No earlier approved evaluation exists.'} for issue in current_mistakes]
        return result

    previous_topics=sorted(set(previous_record.get('comparison_topics',[]) or []))
    result['previous_topics']=previous_topics
    overlap=bool(set(previous_topics).intersection(current_topics))
    result['topic_comparability']='comparable' if overlap else 'not_comparable'
    result['comparability_reason']=(
        f"Shared topic evidence: {', '.join(sorted(set(previous_topics).intersection(current_topics)))}."
        if overlap else
        f"Prior evidence is about {', '.join(previous_topics) or 'an unknown topic'}; current evidence is about {', '.join(current_topics) or 'an unknown topic'}. These submissions are not comparable."
    )
    previous_mistakes=list(dict.fromkeys(previous_record.get('mistakes',[]) or []))
    if not overlap:
        result['previous_mistake_comparison']=[{'mistake':issue,'status':'not_comparable','reason':f"Prior topic(s) {', '.join(previous_topics) or 'unknown'} do not match current topic(s) {', '.join(current_topics) or 'unknown'}."} for issue in previous_mistakes]
        result['current_mistake_comparison']=[{'mistake':issue,'status':'not_comparable','reason':'Current finding belongs to a different or unverified task domain.'} for issue in current_mistakes]
        result['improvement_status']='not_established'
        result['improvement_reason']='The tasks are not comparable, so the available evidence cannot establish learning improvement.'
        return result

    normalized_current={issue.strip().lower() for issue in current_mistakes}
    resolved=[]
    for issue in previous_mistakes:
        if issue.strip().lower() in normalized_current:
            status,reason='repeated','The same observed finding is present in the current evidence.'
        elif 'syntax error' in issue.lower() and current_compilation == 'Valid Python syntax':
            status,reason='resolved','Current static Python parsing succeeds; this directly addresses the prior syntax finding.'
            resolved.append(issue)
        else:
            status,reason='not_comparable','The available current evidence does not establish whether this prior finding was resolved.'
        result['previous_mistake_comparison'].append({'mistake':issue,'status':status,'reason':reason})
    previous_normalized={issue.strip().lower() for issue in previous_mistakes}
    for issue in current_mistakes:
        status='repeated' if issue.strip().lower() in previous_normalized else 'new'
        result['current_mistake_comparison'].append({'mistake':issue,'status':status,'reason':'Compared against the latest approved evaluation in the same topic domain.'})
    result['improved_concepts']=resolved
    result['improvement_status']='supported' if resolved else 'not_established'
    result['improvement_reason']=('Specific static evidence resolved a prior syntax finding.' if resolved else 'No direct current evidence establishes that a previous finding was resolved; a score difference is insufficient.')
    return result
