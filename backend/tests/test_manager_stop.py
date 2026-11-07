"""Stop must leave a terminal status, even when the pipeline never finishes."""

from echo.pipeline.manager import SessionManager
from echo.schemas import SessionConfig


class _Runner:
    def __init__(self, done: bool):
        self.session_id = "ses_test"
        self.done = done
        self.source = type("Source", (), {"simulated": False})()
        self.calibration = None
        self.result = type("Result", (), {"error": None})()
        self.stop_calls = 0

    def wait(self, timeout: float | None = None) -> bool:
        return self.done

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_calls += 1


def _manager(settings, db):
    manager = SessionManager(settings, db, lambda _text: None, lambda _frame: None, lambda: False)
    manager.config = SessionConfig(source="file", file_id="vid_test")
    return manager


def test_stop_releases_a_runner_that_never_finishes(settings, db):
    manager = _manager(settings, db)
    runner = _Runner(done=False)
    manager.runner = runner

    status = manager.stop()

    assert runner.stop_calls == 1
    assert status.state == "stopped"
    assert status.session_id == "ses_test"
    assert manager.runner is None
    assert manager.stop().state == "idle"


def test_stop_of_a_finished_runner_reports_stopped(settings, db):
    manager = _manager(settings, db)
    runner = _Runner(done=True)
    manager.runner = runner

    status = manager.stop()

    assert runner.stop_calls == 0
    assert status.state == "stopped"
    assert manager.runner is runner
