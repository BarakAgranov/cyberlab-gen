"""Smoke test: verify the top-level package imports cleanly."""


def test_package_imports() -> None:
    import cyberlab_gen

    assert cyberlab_gen.__name__ == "cyberlab_gen"
