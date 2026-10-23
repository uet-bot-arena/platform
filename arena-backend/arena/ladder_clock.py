"""Fixed local-day round boundaries (Vietnam UTC+7 has no DST)."""
from datetime import datetime, timedelta, timezone

VN = timezone(timedelta(hours=7))


def boundary(now_ms, hours=2, *, next=False):
    if type(hours) is not int or not 1 <= hours <= 24:
        raise ValueError('Round hours must be an integer from 1 through 24')
    local=datetime.fromtimestamp(now_ms/1000,VN)
    midnight=local.replace(hour=0,minute=0,second=0,microsecond=0)
    hour=(local.hour//hours+int(next))*hours
    if hour>=24:
        return int((midnight+timedelta(days=1)).timestamp()*1000)
    return int((midnight+timedelta(hours=hour)).timestamp()*1000)
