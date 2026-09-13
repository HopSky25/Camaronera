import sys
import types
from pathlib import Path

# Import pure services without importing the Odoo addon entry point.
ROOT=Path(__file__).resolve().parents[1]/'l10n_ec_sri_community'
module=types.ModuleType('sri_core');module.__path__=[str(ROOT/'services')]
sys.modules['sri_core']=module
