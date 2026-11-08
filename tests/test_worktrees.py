"""Each git worktree gets its own dev stack, so parallel sessions can't replace
each other's running code. Builds a throwaway repo with a real worktree and asks
`scripts/dev.sh info` what each checkout would use. No docker needed."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                   check=True, capture_output=True)


def info(checkout, env=None):
    out = subprocess.run([str(checkout / "scripts" / "dev.sh"), "info"], cwd=checkout,
                         check=True, capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(checkout), **(env or {})}).stdout
    return dict(line.split("=", 1) for line in out.strip().splitlines())


@pytest.fixture()
def repo(tmp_path):
    main = tmp_path / "teslamate-expenses"
    (main / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "dev.sh", main / "scripts" / "dev.sh")
    git(main, "init", "-q", "-b", "main")
    git(main, "add", ".")
    git(main, "commit", "-q", "-m", "init")
    return main


def worktree(main, name):
    path = main / ".claude" / "worktrees" / name
    git(main, "worktree", "add", "-q", "-b", f"worktree-{name}", str(path))
    return path


def test_main_checkout_keeps_the_standard_names(repo):
    i = info(repo)
    assert i["project"] == "teslamate-expenses-dev"
    assert i["base_path"] == "/expenses-dev"
    assert (i["ui_port"], i["grafana_port"]) == ("8089", "8090")
    assert i["image"] == "teslamate-expenses:dev"


def test_worktrees_get_their_own_stack(repo):
    a, b = info(worktree(repo, "fix-odometer")), info(worktree(repo, "Feature_Two"))
    assert a["project"] == "teslamate-expenses-dev-fix-odometer"
    assert a["base_path"] == "/expenses-dev/fix-odometer"
    assert b["project"] == "teslamate-expenses-dev-feature-two"   # sanitised for docker
    main = info(repo)
    for key in ("project", "base_path", "ui_port", "grafana_port", "image", "router_suffix"):
        assert len({main[key], a[key], b[key]}) == 3, key


def test_worktree_routes_outrank_the_main_dev_stack(repo):
    main, wt = info(repo), info(worktree(repo, "x"))
    # /expenses-dev/ (main) is a prefix of /expenses-dev/x/, so the worktree must win.
    assert int(wt["ui_priority"]) > int(main["grafana_priority"]) > int(main["ui_priority"])
    assert int(wt["grafana_priority"]) > int(wt["ui_priority"])


def test_reserved_or_unusable_worktree_names_are_refused(repo):
    for bad in ("grafana", "---"):
        path = worktree(repo, bad)
        r = subprocess.run([str(path / "scripts" / "dev.sh"), "info"], cwd=path,
                           capture_output=True, text=True)
        assert r.returncode != 0 and "DEV_INSTANCE" in r.stderr


def test_instance_can_be_named_explicitly(repo):
    assert info(repo, {"DEV_INSTANCE": "demo"})["base_path"] == "/expenses-dev/demo"
