#!/usr/bin/env python3
"""Mutants of unacked_analysis.py (F5, the probe's no-acknowledge modes, 2026-11-27): runs
test_unacked_analysis.py --mutants, so a run over every *_mutants.py file runs them too. Exit 0
when every mutant is killed by a check and the control passes.

    python3.13 -B test_unacked_analysis_mutants.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    sys.exit(subprocess.run([sys.executable, "-B", os.path.join(HERE, "test_unacked_analysis.py"),
                             "--mutants"]).returncode)
