"""Structural tests for the shared sidebar nav table (no Streamlit render)."""

from pathlib import Path

from core.shared_sidebar import _NAV_GROUPS

ROOT = Path(__file__).resolve().parent.parent


def _items():
    return [item for _, items in _NAV_GROUPS for item in items]


def test_nav_keys_unique_and_paths_exist():
    items = _items()
    keys = [k for _, _, _, k in items]
    assert len(keys) == len(set(keys))
    for path, _, _, _ in items:
        assert (ROOT / path).exists(), f"nav points at missing page: {path}"


def test_nav_covers_every_page():
    nav_paths = {p for p, _, _, _ in _items()}
    expected = {"app.py"} | {
        f"pages/{p.name}" for p in (ROOT / "pages").glob("*.py")
    }
    assert nav_paths == expected


def test_active_marker_only_for_matching_key():
    # The label rule used in render_sidebar_nav: bold + ▸ iff key == current.
    items = _items()
    for current in ("", "blog", "memory"):
        marked = [
            label for _, label, _, key in items
            if (bool(current) and key == current)
        ]
        if current:
            assert len(marked) == 1
        else:
            assert marked == []