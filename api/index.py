"""Vercel Serverless Function entry point for MediBridge REST API Gateway."""

import os
import sys
from pathlib import Path

# Add project root and src/ to sys.path so medibridge and medibridge_db resolve properly in serverless
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.main import app
