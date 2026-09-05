"""Bale Messenger platform adapter for Hermes Agent.

Public re-export of the :func:`register` entry point used by the Hermes
plugin manager.
"""

from .adapter import register

__all__ = ["register"]