import pytest

from app.core import greet


def test_greet_returns_greeting() -> None:
    assert greet("  Ada ") == "Hello, Ada!"


def test_greet_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        greet("   ")
