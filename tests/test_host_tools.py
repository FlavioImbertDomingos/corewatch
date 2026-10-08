import os
import shutil
import subprocess
from pathlib import Path

import pytest

from corewatch.textfile_server import render

ROOT = Path(__file__).resolve().parents[1]


def read_metrics(path):
    return {line.rsplit(" ", 1)[0]: float(line.rsplit(" ", 1)[1])
            for line in Path(path).read_text().splitlines() if line and not line.startswith("#")}


def test_textfile_server_concatenates_and_reports_age(tmp_path):
    (tmp_path / "a.prom").write_text("a_metric 1\n")
    (tmp_path / "b.prom").write_text("b_metric 2")  # no trailing newline
    (tmp_path / "ignored.txt").write_text("nope 9\n")
    now = (tmp_path / "a.prom").stat().st_mtime + 120
    text = render(str(tmp_path), now=now)
    assert "a_metric 1\nb_metric 2\n" in text
    assert "nope" not in text
    assert 'corewatch_textfile_age_seconds{file="a.prom"} 120' in text


def test_textfile_server_empty_directory(tmp_path):
    assert render(str(tmp_path)) == ""


def run_job(tmp_path, *command):
    env = dict(os.environ, COREWATCH_DIR=str(tmp_path))
    return subprocess.run(
        ["sh", str(ROOT / "collectors/common/corewatch_job.sh"), "nightly", "--max-age", "93600", "--", *command],
        env=env,
    ).returncode


def test_job_wrapper_success_then_failure(tmp_path):
    assert run_job(tmp_path, "true") == 0
    m = read_metrics(tmp_path / "job_nightly.prom")
    success = m['corewatch_job_last_success_seconds{job_name="nightly"}']
    assert success > 0
    assert m['corewatch_job_last_exit_code{job_name="nightly"}'] == 0
    assert m['corewatch_job_max_age_seconds{job_name="nightly"}'] == 93600
    assert m['corewatch_job_running{job_name="nightly"}'] == 0

    assert run_job(tmp_path, "sh", "-c", "exit 7") == 7
    m = read_metrics(tmp_path / "job_nightly.prom")
    assert m['corewatch_job_last_exit_code{job_name="nightly"}'] == 7
    # a failed run keeps the earlier success time
    assert m['corewatch_job_last_success_seconds{job_name="nightly"}'] == success


def test_job_wrapper_rejects_bad_names(tmp_path):
    env = dict(os.environ, COREWATCH_DIR=str(tmp_path))
    result = subprocess.run(
        ["sh", str(ROOT / "collectors/common/corewatch_job.sh"), 'bad"name', "--", "true"],
        env=env, capture_output=True,
    )
    assert result.returncode == 64


@pytest.mark.skipif(shutil.which("ksh") is None, reason="ksh not installed")
def test_aix_collector_against_recorded_output(tmp_path):
    """Runs the real ksh script with stub AIX commands that print recorded output."""
    env = dict(os.environ, COREWATCH_DIR=str(tmp_path),
               PATH=f"{ROOT / 'tests/fake_aix'}:{os.environ['PATH']}")
    subprocess.run(["ksh", str(ROOT / "collectors/aix/corewatch_aix.sh")], env=env, check=True)
    m = read_metrics(tmp_path / "aix.prom")
    assert m['aix_oslevel_info{oslevel="7300-02-01-2346"}'] == 1
    assert m['aix_errpt_entries{type="P",class="H"}'] == 2
    assert m['aix_errpt_entries{type="P",class="S"}'] == 1
    assert m['aix_errpt_entries{type="T",class="H"}'] == 0
    assert m['aix_vg_stale_pps{vg="datavg"}'] == 12
    assert m['aix_vg_stale_pps{vg="rootvg"}'] == 0
    assert m['aix_vg_free_bytes{vg="rootvg"}'] == 27584 * 1048576
    assert m['aix_mpio_paths{status="Failed"}'] == 1
    assert m['aix_mpio_paths{status="Enabled"}'] == 3
    assert m['aix_mpio_paths{status="Missing"}'] == 0
    assert m["aix_paging_space_used_ratio"] == 0.03
    assert m['aix_filesystem_used_ratio{mountpoint="/db2/logs"}'] == 0.95
    assert 'aix_filesystem_used_ratio{mountpoint="/proc"}' not in m
    assert m["aix_interim_fixes"] == 1
