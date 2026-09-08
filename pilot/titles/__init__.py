"""Title profiles: which cartridge this is, and the facts its opening needs."""
from .contract import usable, validate_title
from .pick import TITLES, header_title, pick_title, title_by_id

__all__ = ["TITLES", "header_title", "pick_title", "title_by_id",
           "usable", "validate_title"]
