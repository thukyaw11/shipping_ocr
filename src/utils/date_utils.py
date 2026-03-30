import pytz
from datetime import datetime


def to_local_time(utc_dt: datetime, timezone_str: str):
    if not utc_dt:
        return None
    try:
        utc_dt = utc_dt.replace(tzinfo=pytz.utc)
        local_tz = pytz.timezone(timezone_str)
        return utc_dt.astimezone(local_tz)
    except Exception:
        return utc_dt
