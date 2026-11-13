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


def run_destroy(checkout, tmp_path):
    """Run `dev.sh destroy` with a stand-in docker that logs each call and whether .dev/env existed."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "docker"
    fake.write_text('#!/bin/sh\necho "$* env=$([ -f .dev/env ] && echo yes || echo no)" >> "$FAKE_DOCKER_LOG"\n')
    fake.chmod(0o755)
    log = tmp_path / "docker.log"
    subprocess.run([str(checkout / "scripts" / "dev.sh"), "destroy"], cwd=checkout, check=True, capture_output=True,
                   env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(checkout), "FAKE_DOCKER_LOG": str(log)})
    return log.read_text().splitlines()


def test_destroy_removes_the_stacks_folder_and_nothing_else(repo, tmp_path):
    wt = worktree(repo, "x")
    (wt / ".dev" / "screenshots").mkdir(parents=True)
    (wt / ".dev" / "screenshots" / "page.png").write_text("png")
    (repo / ".dev").mkdir()
    (repo / ".dev" / "env").write_text("the main checkout's own stack")
    calls = run_destroy(wt, tmp_path)
    assert not (wt / ".dev").exists()
    assert (wt / "scripts" / "dev.sh").exists() and (wt / ".git").exists()
    assert (repo / ".dev" / "env").read_text() == "the main checkout's own stack"
    # The stack comes down first: compose needs the passwords in .dev/env that are then deleted.
    (down,) = [c for c in calls if " down " in f" {c} "]
    assert "-v" in down and down.endswith("env=yes")


def test_destroy_works_when_the_stack_was_never_started(repo, tmp_path):
    wt = worktree(repo, "x")
    assert not (wt / ".dev").exists()
    run_destroy(wt, tmp_path)
    assert not (wt / ".dev").exists()


# ---- the dev stack's invoice folder on a mounted share (DEV_INVOICES_SHARE) -----------

def test_a_share_gives_each_stack_its_own_folder_in_it(repo, tmp_path):
    share = tmp_path / "share"
    env = {"DEV_INVOICES_SHARE": f"{share}/"}                 # a trailing slash makes no difference
    main, a, b = info(repo, env), info(worktree(repo, "fix-odometer"), env), info(worktree(repo, "Feature_Two"), env)
    assert main["invoices"] == f"{share}/main"
    assert a["invoices"] == f"{share}/fix-odometer" and b["invoices"] == f"{share}/feature-two"
    assert len({main["invoices"], a["invoices"], b["invoices"]}) == 3, "stacks must not share a folder"


def test_without_a_share_the_folder_is_the_checkouts_own(repo):
    assert info(repo)["invoices"] == f"{repo}/.dev/invoices"


def test_an_explicit_folder_beats_the_share(repo, tmp_path):
    env = {"DEV_INVOICES_SHARE": str(tmp_path / "share"), "DEV_INVOICES_DIR": str(tmp_path / "chosen")}
    assert info(repo, env)["invoices"] == str(tmp_path / "chosen")


def test_the_demo_stack_never_uses_the_share(repo, tmp_path):
    out = subprocess.run([str(repo / "scripts" / "dev.sh"), "demo", "info"], cwd=repo, check=True, capture_output=True,
                         text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(repo),
                                         "DEV_INVOICES_SHARE": str(tmp_path / "share")}).stdout
    assert f"invoices={repo}/.dev/demo-invoices" in out


def test_a_share_that_is_not_mounted_is_refused_and_nothing_is_made_in_it(repo, tmp_path):
    """The folder is only an empty mount point on the server's own disk: don't fill it."""
    share = tmp_path / "mount-point"
    share.mkdir()
    r = subprocess.run([str(repo / "scripts" / "dev.sh"), "sample-invoices"], cwd=repo, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(repo), "DEV_INVOICES_SHARE": str(share)})
    assert r.returncode != 0 and "isn't mounted" in r.stderr and str(share) in r.stderr
    assert list(share.iterdir()) == []


def test_a_share_that_is_not_there_is_refused_and_never_created(repo, tmp_path):
    share = tmp_path / "gone"
    r = subprocess.run([str(repo / "scripts" / "dev.sh"), "sample-invoices"], cwd=repo, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(repo), "DEV_INVOICES_SHARE": str(share)})
    assert r.returncode != 0 and not share.exists()


def test_destroy_does_not_need_the_share_and_leaves_its_files(repo, tmp_path):
    """Taking a stack down must work with the NAS off, and never deletes what is on the share."""
    wt = worktree(repo, "old-branch")
    share = tmp_path / "share"
    (share / "old-branch").mkdir(parents=True)
    (share / "old-branch" / "kept.pdf").write_text("pdf")
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    out = subprocess.run([str(wt / "scripts" / "dev.sh"), "destroy"], cwd=wt, check=True, capture_output=True, text=True,
                         env={"PATH": f"{fake.parent}:/usr/bin:/bin", "HOME": str(wt), "DEV_INVOICES_SHARE": str(share)})
    assert (share / "old-branch" / "kept.pdf").read_text() == "pdf"
    assert "left" in out.stdout and str(share / "old-branch") in out.stdout
