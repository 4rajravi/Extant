"""The offline ablation arms (4 to 6): local models and the reduced-corpus build.

Needs the optional dependency group: ``uv sync --group ablation`` (sentence-transformers and
PyTorch). Never installed in CI, never imported by serving, and never imported by the gate:
these arms run on the Mac, on the sample, and what they find is a finding for the writeup
rather than a component to deploy.
"""
