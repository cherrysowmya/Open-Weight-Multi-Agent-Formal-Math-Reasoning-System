"""Local Lean Agent MVP."""

from .config import AppConfig, load_config
from .orchestrator import ProofAgent

__all__ = ["AppConfig", "ProofAgent", "load_config"]
__version__ = "0.4.0"
