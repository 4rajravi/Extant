"""What the middleware raises, in a module that imports nothing, so a node can catch these
without loading the database driver the counter needs."""

from __future__ import annotations


class CapReachedError(RuntimeError):
    """Today's answered questions have reached the daily cap; no model call may run."""


class ModelUnavailableError(RuntimeError):
    """The provider failed after the one retry the policy allows."""
