"""Monotonic intervals of real backend phase transitions, not estimated progress."""
import time

from .store import now


class JobStageTimer:
    def __init__(self, job, clock=time.perf_counter, wall=now):
        self.job, self.clock, self.wall = job, clock, wall
        self.started = clock()
        self.stages = []
        self.current = None
        self.finished = None

    def _close(self, at, status):
        if self.current is not None:
            row = dict(self.current)
            row['elapsedSeconds'] = round(at-row.pop('_started'), 6)
            row['status'] = status
            self.stages.append(row)
            self.current = None

    def enter(self, name):
        if self.finished is not None:
            return
        if self.current is not None and self.current['name'] == name:
            return
        at = self.clock()
        self._close(at, 'finished')
        self.current = dict(name=name, startedAt=self.wall(), _started=at)

    def finish(self, final_job):
        if self.finished is not None:
            return self.finished
        at = self.clock()
        self._close(at, final_job['status'])
        self.finished = dict(schemaVersion=1, policy='backend-phase-intervals-v1',
            jobId=self.job['id'], projectId=self.job['projectId'], jobKind=self.job['kind'],
            inputRevision=self.job.get('inputRevision'), status=final_job['status'],
            createdAt=final_job['createdAt'], startedAt=final_job.get('startedAt'),
            finishedAt=final_job.get('finishedAt'), elapsedSeconds=round(at-self.started, 6),
            stages=self.stages,
            scope='Actual backend execution and phase intervals; terminal status is not engineering acceptance. '
                  'Queue time uses stored timestamps. External agent/model latency is not measured. '
                  'A hard process stop can leave only existing job.phase events, not a finalized timing artifact.')
        return self.finished
