"""Put the code under test (/code, mounted read-only) on the import path."""

import os
import sys

CODE_DIR = os.environ.get("MEDUSA_CODE_DIR", "/code")
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)
