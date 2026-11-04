"""Read-only process observations from job facts; never judge Agent intent."""
from collections import Counter, defaultdict


def outcome(job):
    status = job.get('status')
    if status in {'queued', 'running'}:
        return 'active'
    if status in {'cancelled', 'interrupted'}:
        return 'stopped'
    if status == 'failed':
        return 'failed'
    if status != 'completed':
        return 'unknown'
    result = job.get('result') or {}
    if result.get('adoptionMode')=='review' and result.get('readyForAdoption') and not result.get('adopted'):
        return 'awaiting_review'
    # A deliberately adopted PCB draft is progress, even if it is not finished.
    if result.get('adopted') is True:
        return 'adopted'
    if result.get('adopted') is False:
        return 'not_adopted'
    if result.get('checksPassed') is False:
        return 'check_failed'
    return 'completed'


def process_review(project, jobs, revisions=None):
    heads = {project.get('headRevisionId'), *project.get('moduleHeads', {}).values()} - {None}
    reviewed={r.get('createdByJobId') for r in revisions or [] if (r.get('validation') or {}).get('adoptionMode')=='review' and (r.get('validation') or {}).get('adopted')}
    def current_outcome(job):
        if job['id'] in reviewed:return 'adopted_after_review'
        state=outcome(job)
        return 'historical_candidate' if state=='awaiting_review' and job.get('inputRevision') not in heads else state
    ordered = sorted(jobs, key=lambda j: (j.get('createdAt', ''), j['id']))
    groups = defaultdict(list)
    counts = Counter()
    for job in ordered:
        counts[current_outcome(job)] += 1
        if job.get('inputRevision'):
            groups[(job['kind'], job['inputRevision'])].append(job)
    observations = []
    rejected = {'failed', 'not_adopted', 'check_failed'}
    for (kind, revision), attempts in groups.items():
        unsuccessful = [j for j in attempts if current_outcome(j) in rejected]
        if len(unsuccessful) < 2:
            continue
        last = attempts[-1]
        # Later successful work on the same input resolves the repeat warning;
        # old revisions remain history, not current blockers.
        attention = revision in heads and current_outcome(last) in rejected
        observations.append(dict(kind=kind, inputRevision=revision,
            unsuccessfulCount=len(unsuccessful), attemptCount=len(attempts),
            jobIds=[j['id'] for j in attempts], latestJobId=last['id'], latestOutcome=current_outcome(last),
            currentInput=revision in heads, needsAttention=attention,
            status='review_before_retry' if attention else 'history',
            latestCreatedAt=last.get('createdAt'),
            reason='same_input_multiple_unsuccessful_attempts',
            action='compare_inputs_and_feedback_before_retry'))
    observations.sort(key=lambda row: (row['needsAttention'], row['latestCreatedAt'] or ''), reverse=True)
    return dict(jobCount=len(jobs), outcomeCounts=dict(counts),
        repeatedAttemptCount=len(observations), attentionCount=sum(r['needsAttention'] for r in observations),
        repeatedAttempts=observations[:8], omittedGroupCount=max(0, len(observations)-8),
        scope='Same kind and input revision only; changed parameters may be useful. Not a diagnosis or execution gate.',
        nextAction='read_job_inputs_and_reports; change_evidence_or_strategy_before_repeating')


def pcb_read_coverage(snapshot, offset, limit):
    """Make late-page zones discoverable without pretending a page is a whole board."""
    objects = snapshot['objects']
    page = objects[offset:offset + limit]
    complete = offset == 0 and len(page) == len(objects)
    inventory = [dict(objectId=z['id'], expectedHash=z['sha256'],
                      net=z.get('net'), layer=z.get('layer'), copperLayers=list(z.get('copperLayers', [])),
                      ruleArea=z.get('ruleArea', False)) for z in snapshot.get('zones', [])]
    return dict(objectCoverage=dict(complete=complete, returned=len(page), total=len(objects),
                                   offset=offset, remainingAfterPage=max(0, len(objects)-offset-len(page)),
                                   categoriesOnPage=dict(Counter(o.get('type', 'unknown') for o in page)),
                                   instruction='pin_revision_and_read_all_pages_before_whole_board_conclusions'),
                zoneInventory=dict(complete=True, count=len(inventory), objects=inventory,
                                   revisionId=snapshot['revisionId'], boardSha256=snapshot['boardSha256'],
                                   automaticYieldConsent=False,
                                   scope='Identity inventory only. Explicitly review nets and copper before choosing yieldZones.'))
