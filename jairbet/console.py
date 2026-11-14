"""Small coloured console logging helpers for command-line tools and the bot.

Colours are only emitted on interactive terminals, so journald logs stay clean.
Set ``NO_COLOR`` to disable them everywhere.
"""

import os
import sys
from typing import TextIO


def _print(message: str, colour: str, stream: TextIO | None = None) -> None:
    """Print a message using an ANSI colour when the stream supports it.

    Args:
        message: Text to display.
        colour: ANSI colour sequence without the escape prefix.
        stream: Output stream to receive the message (stdout by default).
    """
    target = stream or sys.stdout
    use_colour = target.isatty() and "NO_COLOR" not in os.environ
    text = f"\033[{colour}m{message}\033[0m" if use_colour else message
    print(text, file=target, flush=True)


def info(message: str) -> None:
    """Print an informational message in blue.

    Args:
        message: Text to display.
    """
    _print(message, "34")


def success(message: str) -> None:
    """Print a success message in green.

    Args:
        message: Text to display.
    """
    _print(message, "32")


def warning(message: str) -> None:
    """Print a warning message in yellow.

    Args:
        message: Text to display.
    """
    _print(message, "33", sys.stderr)


def error(message: str) -> None:
    """Print an error message in red.

    Args:
        message: Text to display.
    """
    _print(message, "31", sys.stderr)
