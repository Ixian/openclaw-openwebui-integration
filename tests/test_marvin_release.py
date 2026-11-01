"""The release manifest and the real-stack workflow must pin the same image."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('marvin_release', ROOT / 'scripts/marvin_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_the_release_image_is_the_image_exercised_in_ci():
    workflow = (ROOT / '.github/workflows/marvin-ci.yml').read_text()
    assert 'owui_image: ' + release.IMAGE in workflow
    assert "openclaw_version: '2026.10.9'" in workflow
