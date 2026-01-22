from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True, frozen=True)
class UserWarning:
    user_id: int
    warning: str
    updated_at: datetime
