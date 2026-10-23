"""Sandbox interface for Linux/WSL2 and Windows; uploaded code always runs in isolation."""
from arena.sandbox.artifacts import cached_cpp_build, remove_tree, sdk_hash, seal

BACKENDS = ("bwrap", "windows")


def get_backend(name, **options):
    """Return a native sandbox backend; options are specific to that backend."""
    if name == "bwrap":
        from arena.sandbox.bwrap import BwrapBackend
        return BwrapBackend(**options)
    if name == "windows":
        from arena.sandbox.windows import WindowsBackend
        return WindowsBackend(**options)
    raise ValueError("Unknown sandbox backend: " + str(name))
