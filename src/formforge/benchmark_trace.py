"""Count actual caller dispatch attempts, independently of jobs and native workers.

Trace records contain bindings, timings and safe result identifiers, never request
headers, request/response bodies or exception messages. A trace belongs to one
run/case/input fingerprint. Callers retain the complete responses separately.
"""
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from .store import now


ACTORS = ('external-agent', 'benchmark-runner')
PURPOSES = ('business', 'poll', 'verification')
BINDING_FIELDS = ('runId', 'caseId', 'inputFingerprint')


def _binding(events):
    binding = None
    for event in events:
        current = {key: event[key] for key in BINDING_FIELDS}
        if any(not isinstance(value, str) or not value.strip() for value in current.values()):
            raise ValueError('Trace requires nonempty run, case and input bindings.')
        if binding is not None and current != binding:
            raise ValueError('Cannot mix runId, caseId or inputFingerprint in one trace.')
        binding = current
    return binding


def summarize_trace(events):
    """Summarize recorded API attempts; absent external-agent evidence stays null."""
    events = list(events)
    binding = _binding(events)
    purposes = {purpose: 0 for purpose in PURPOSES}
    actors = {actor: 0 for actor in ACTORS}
    sequences = set()
    for event in events:
        if event.get('schemaVersion') != 1 or event.get('actor') not in ACTORS or event.get('purpose') not in PURPOSES:
            raise ValueError('Unsupported operation trace record.')
        sequence = event.get('sequence')
        if not isinstance(sequence, int) or sequence < 1 or sequence in sequences:
            raise ValueError('Operation trace sequences must be positive and unique.')
        sequences.add(sequence)
        if event.get('outcome') not in ('success', 'error'):
            raise ValueError('Operation trace has no completed call outcome.')
        actors[event['actor']] += 1
        purposes[event['purpose']] += 1
    return dict(schemaVersion=1, **(binding or {}), actualCalls=len(events),
                externalAgentCalls=actors['external-agent'] or None,
                runnerCalls=actors['benchmark-runner'], purposeCalls=purposes,
                failedCalls=sum(event['outcome'] == 'error' for event in events),
                callElapsedSeconds=round(sum(event['elapsedSeconds'] for event in events), 6))


class OperationTrace:
    def __init__(self, path, *, run_id, case_id, input_fingerprint, actor,
                 clock=time.perf_counter, wall=now):
        if actor not in ACTORS:
            raise ValueError('actor must explicitly identify external-agent or benchmark-runner.')
        self.path = Path(path)
        self.binding = dict(runId=run_id, caseId=case_id, inputFingerprint=input_fingerprint)
        _binding([self.binding])
        self.actor, self.clock, self.wall = actor, clock, wall
        existing = self.read()
        self.sequence = max((event['sequence'] for event in existing), default=0)

    def read(self):
        events = [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines() if line.strip()] if self.path.exists() else []
        summary = summarize_trace(events)
        if events and any(summary[key] != self.binding[key] for key in BINDING_FIELDS):
            raise ValueError('Cannot append another run, case or input fingerprint to this trace.')
        return events

    def _start(self, transport, operation, purpose):
        if purpose not in PURPOSES:
            raise ValueError('purpose must be business, poll or verification.')
        self.sequence += 1
        return dict(schemaVersion=1, **self.binding, actor=self.actor, sequence=self.sequence,
                    transport=transport, operation=operation, purpose=purpose,
                    startedAt=self.wall(), outcome='error'), self.clock()

    def _finish(self, event, started):
        event.update(finishedAt=self.wall(), elapsedSeconds=round(self.clock()-started, 6))
        # Check existing bindings again before appending, including a reused CLI path.
        self.read()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(',', ':'))+'\n')

    @staticmethod
    def _result_identifiers(event, data):
        if not isinstance(data, dict):
            return
        for field in ('projectId', 'jobId', 'revisionId', 'boardSha256'):
            if isinstance(data.get(field), str):
                event[field] = data[field]
        identity = data.get('id')
        if isinstance(identity, str) and identity.startswith('j_'):
            event['jobId'] = identity
        result = data.get('result')
        if isinstance(result, dict) and isinstance(result.get('revisionId'), str):
            event['revisionId'] = result['revisionId']

    async def http(self, client, method, path, *, purpose='business', **kwargs):
        # Query values, headers and bodies can contain credentials and are omitted.
        event, started = self._start('http', method.upper()+' '+urlsplit(str(path)).path, purpose)
        try:
            response = await client.request(method, path, **kwargs)
            event['httpStatus'] = response.status_code
            event['outcome'] = 'success' if 200 <= response.status_code < 300 else 'error'
            try:
                self._result_identifiers(event, response.json())
            except ValueError:
                pass
            return response
        except BaseException as error:
            event['errorType'] = type(error).__name__
            raise
        finally:
            self._finish(event, started)

    async def mcp(self, session, name, arguments=None, *, purpose='business'):
        event, started = self._start('mcp', name, purpose)
        try:
            response = await session.call_tool(name, arguments or {})
            event['outcome'] = 'error' if response.isError else 'success'
            self._result_identifiers(event, getattr(response, 'structuredContent', None))
            return response
        except BaseException as error:
            event['errorType'] = type(error).__name__
            raise
        finally:
            self._finish(event, started)
