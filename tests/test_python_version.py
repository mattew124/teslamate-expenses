"""One Python version for the image and for CI, so they can't drift apart."""
import re

from tests.test_dev_stack import ROOT


def test_ci_and_image_use_the_same_python():
    image = re.search(r"^FROM python:(\d+\.\d+)-slim", (ROOT / "Dockerfile").read_text(), re.M).group(1)
    ci = re.search(r'python-version:\s*"?(\d+\.\d+)"?', (ROOT / ".github/workflows/ci.yml").read_text()).group(1)
    assert image == ci


def test_workflow_actions_are_pinned_to_commits():
    """A tag can be moved; a full commit SHA can't. Dependabot keeps the pins current."""
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        for line in path.read_text().splitlines():
            m = re.match(r"\s*-?\s*uses:\s*(\S+)", line)
            if m and not m.group(1).startswith("./"):
                assert re.search(r"@[0-9a-f]{40}$", m.group(1)), f"{path.name}: {m.group(1)}"
