"""Start real graders next to a test hub (same code path as `python -m arena.grader`)."""
from pathlib import Path
import tempfile
import time

from arena.grader import Grader


def start_grader(app, origin, backend=None, slots=2, name="integration grader", backend_options=None, cpus=None):
    """Create a grader token on the hub, connect a grader to it and wait until it is online."""
    token = app.state.service.create_grader(name, "integration")["token"]
    grader = Grader(origin, token, backend, slots, cpus=cpus, state_dir=Path(tempfile.mkdtemp(prefix="grader-")),
                    backend_options=backend_options)
    grader.start()
    until = time.monotonic() + 30
    while not any(g["online"] and g["name"] == name for g in app.state.service.graders()) and time.monotonic() < until:
        time.sleep(0.1)
    assert any(g["online"] and g["name"] == name for g in app.state.service.graders()), "grader did not come online"
    return grader


def stop_grader(grader):
    grader.stopping.set()
    grader.release_all()
