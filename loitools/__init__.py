"""Tools for Castlevania: Lament of Innocence (NTSC-U, SLUS-20733) running in PCSX2."""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")          # decoded maps and play data (git-ignored)
CONFIG_FILE = os.path.join(ROOT, "config.json")  # settings for both tools (git-ignored)
