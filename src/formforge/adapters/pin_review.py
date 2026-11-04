"""Version-bound presentation dispositions; never an electrical pass or suppression."""
from ..store import DomainError


def evaluate(entries,revision_id,pages):
    seen=set();rows=[]
    for entry in entries:
        key=(entry['sheet'],entry['reference'],entry['pin'])
        if key in seen:raise DomainError('invalid_input','同一图页引脚不能重复登记复核理由。')
        seen.add(key)
        page=pages.get(entry['sheet']);reason=None;pin=None
        if not page:reason='sheet_missing'
        elif entry['reviewedRevisionId']!=revision_id:reason='revision_changed'
        elif entry['expectedSheetHash']!=page['sha256']:reason='sheet_changed'
        else:
            matches=[p for p in page['readability'].get('pinConnections',[]) if p['reference']==entry['reference'] and p['pin']==entry['pin']]
            if len(matches)!=1:reason='pin_missing_or_unavailable'
            else:pin=matches[0]
        rows.append(dict(**entry,status='needs_review' if reason else 'open' if entry['decision']=='needs_layout' else 'recorded',
                         invalidationReason=reason,currentRevisionId=revision_id,currentSheetHash=page['sha256'] if page else None,
                         currentPin=pin,electricalVerdict=False))
    return dict(entries=rows,recorded=sum(r['status']=='recorded' for r in rows),open=sum(r['status']=='open' for r in rows),
                needsReview=sum(r['status']=='needs_review' for r in rows),scope='Only explicitly listed pins; does not suppress readability findings or prove electrical correctness.')
