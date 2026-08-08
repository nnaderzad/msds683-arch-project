"""Offline tests for the 19hz Cloud Run Job runner (pure command builder)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load by file under a unique module name: a bare `import job` collides with
# google_trends_api/job.py, which other tests already cache in sys.modules
# under the name "job" (caught by CI on the full suite).
_JOB_PATH = Path(__file__).resolve().parents[1] / "nineteenhz_api" / "job.py"
_spec = importlib.util.spec_from_file_location("nineteenhz_job", _JOB_PATH)
job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(job)


def test_commands_chains_collect_then_poll_on_the_same_csv():
    cmds = job.commands("/tmp/x.csv")
    assert len(cmds) == 2
    collect, poll = cmds
    assert collect[1].endswith("collect_19hz.py")
    assert poll[1].endswith("poll_ticket_pages.py")
    # step 2 reads exactly the CSV step 1 wrote
    assert collect[collect.index("--output") + 1] == "/tmp/x.csv"
    assert poll[poll.index("--events") + 1] == "/tmp/x.csv"
    # both land bronze — that's the whole point of the scheduled run
    assert "--land-raw" in collect and "--land-raw" in poll


def test_commands_use_the_running_interpreter():
    for argv in job.commands("/tmp/x.csv"):
        assert argv[0] == sys.executable
