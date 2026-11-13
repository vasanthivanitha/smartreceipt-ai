"""Exercise the actual python -m entry point, where functions live in __main__."""
import runpy
from unittest.mock import MagicMock

import pytest
from rq import Queue
from app import db


def test_maintenance_entrypoint_dispatches_importable_job(monkeypatch):
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute.return_value = [(3, 7)]
    session.scalars.return_value = []
    monkeypatch.setattr(db, 'Session', lambda: session)
    enqueue = MagicMock()
    monkeypatch.setattr(Queue, 'enqueue_call', enqueue)

    class StopLoop(BaseException):
        pass

    def stop(_):
        raise StopLoop()

    monkeypatch.setattr('time.sleep', stop)
    with pytest.raises(StopLoop):
        runpy.run_module('app.jobs', run_name='__main__')
    enqueue.assert_called_once()
    assert enqueue.call_args.kwargs['func'] == 'app.jobs.process_receipt'
    assert enqueue.call_args.kwargs['args'] == (3, 7)
