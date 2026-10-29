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


MINUTE = 60_000
DAY = 86_400_000


def next_minute(now_ms):
    return (now_ms // MINUTE + 1) * MINUTE


def round_boundary(now_ms, ladder, *, next=False):
    # Legacy odd-hour calendars reset at midnight; do not turn them into drifting intervals.
    if 'round_minutes' not in ladder:
        return boundary(now_ms, ladder.get('round_hours', 2), next=next)
    minutes = ladder['round_minutes']
    if type(minutes) is not int or not 5 <= minutes <= 10080:
        raise ValueError('Round minutes must be an integer from 5 through 10080')
    step = minutes * MINUTE
    first = ladder.get('first_round_ms')
    if first is None:
        first = next_minute(now_ms)  # Draft preview only; opening persists this anchor.
    if next:
        return first + max(0, (now_ms-first)//step + 1) * step
    return first + max(-1, (now_ms-first)//step) * step


def schedule_preview(ladder, start_ms, deadline_ms):
    first = round_boundary(start_ms, ladder)
    if 'round_minutes' in ladder:
        first = max(first, ladder.get('first_round_ms') if ladder.get('first_round_ms') is not None else next_minute(start_ms))
    if first < start_ms:
        first = round_boundary(start_ms, ladder, next=True)
    if deadline_ms <= first:
        return dict(times=[], rounds=0)
    if 'round_minutes' in ladder:
        step = ladder['round_minutes'] * MINUTE
        count = (deadline_ms-first+step-1)//step
        return dict(times=[first+i*step for i in range(min(5,count))], rounds=count)
    hours = ladder.get('round_hours', 2)
    def before(at):
        local = at+7*3_600_000
        day, offset = divmod(local, DAY)
        return day*len(range(0,24,hours))+sum(h*3_600_000<offset for h in range(0,24,hours))
    times=[];value=first
    while value<deadline_ms and len(times)<5:
        times.append(value);value=round_boundary(value,ladder,next=True)
    return dict(times=times,rounds=before(deadline_ms)-before(start_ms))
