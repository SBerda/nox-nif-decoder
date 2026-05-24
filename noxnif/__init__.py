"""noxnif — reverse-engineered reader for Nox Medical .NIF recordings."""
from .parser import NoxNIF, Channel, A1S_CHANNELS, epoch_us_to_datetime

__all__ = ["NoxNIF", "Channel", "A1S_CHANNELS", "epoch_us_to_datetime"]
__version__ = "0.1.0"
