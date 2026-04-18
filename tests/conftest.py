import os
import sys
from pathlib import Path

# Ensure ./out goes to a tmp dir so tests don't pollute the repo.
os.environ.setdefault("SONGSMITH_OUT", str(Path(__file__).parent / "_out"))

# Make the project root importable in a source layout.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
