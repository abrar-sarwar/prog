"""Root conftest: pytest auto-inserts this file's directory into sys.path,
which lets the test modules import top-level packages (``core``, ``db``).
There is no pyproject.toml / setup.py in this project, so this is the
mechanism we rely on.
"""
