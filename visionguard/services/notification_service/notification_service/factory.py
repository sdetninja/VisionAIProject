from .channels import build_channels
from .config import Config
from .service import NotificationService
from .store import Store


def build(cfg: Config, channels=None, clock=None):
    kw = {"clock": clock} if clock else {}
    return NotificationService(cfg, Store(cfg.db_path), channels or build_channels(cfg.channels), **kw)
