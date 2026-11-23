"""Answer level evaluation (Phase 6): whole answers against real model calls.

Retrieval eval (``evals.retrieval``) needs no model and runs on every push. This costs money,
so it runs on demand, over deliberately sized samples, with a dry run first.
"""
