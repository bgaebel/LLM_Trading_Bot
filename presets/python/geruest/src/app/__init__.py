"""Application package."""

from app.core import greet

__all__ = ["greet", "main"]


def main() -> None:
    print(greet("world"))
