"""Deployment entrypoint: hosting platforms look for a module-level ``app``."""

from webapp import app

__all__ = ["app"]
