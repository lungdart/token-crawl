"""Logging setup.

Two rules, both from the review:
  * every failure is logged in full, with a stack trace — nothing is ever swallowed;
  * the player never sees a Python error, and never sees a fault dressed as a game event.

`FAULT` is the message shown when something breaks. It is deliberately bracketed and
out-of-world so it can't be mistaken for something that happened in the dungeon.
"""
import logging

FAULT = "[Something broke here — this is a fault in the game, not something that happened to you.]"


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
