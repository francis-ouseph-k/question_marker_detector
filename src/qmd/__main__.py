"""Allow ``python -m qmd ...``."""

import sys

from qmd.cli import main

sys.exit(main())
