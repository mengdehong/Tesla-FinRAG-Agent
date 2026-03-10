"""Allow running the evaluation as ``python -m tesla_finrag.evaluation``."""

import sys

from tesla_finrag.evaluation.runner import main

main(sys.argv[1:])
