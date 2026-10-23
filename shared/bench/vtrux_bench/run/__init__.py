"""The chain runner core: one command runs a project's whole bench suite.

A project writes a Suite (model.py) and runs it with
`python -m vtrux_bench.run run --suite <file>:<NAME> --settings <json>`.
"""

from .model import (Check, CheckRun, Figure, Group, Image, KnownFail, Result,
                    Suite, SuiteError, Tol, Verdict, exit_code_judge, validate)
from .engine import RULES, Engine, exit_code
from .record import compare

__all__ = ["Check", "CheckRun", "Figure", "Group", "Image", "KnownFail",
           "Result", "Suite", "SuiteError", "Tol", "Verdict",
           "exit_code_judge", "validate", "RULES", "Engine", "exit_code",
           "compare"]
