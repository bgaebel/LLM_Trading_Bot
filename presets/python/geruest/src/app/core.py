"""Core logic: keep it free of I/O so it stays unit-testable."""


def greet(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    return f"Hello, {name}!"
