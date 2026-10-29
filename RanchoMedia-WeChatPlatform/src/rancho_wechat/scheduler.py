"""Replaceable local scheduling edge; SQLite daily key owns dedup and crash recovery."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from rancho_wechat.editorial import EditorialDesk, EditorialError


def run_forever(desk: EditorialDesk, hour: int = 8, minute: int = 0) -> None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间格式无效")
    while True:
        local = datetime.now(ZoneInfo("Asia/Shanghai"))
        if (local.hour, local.minute) >= (hour, minute):
            try:
                desk.daily(local.date())
                desk.prepare_accepted()
            except EditorialError as error:
                if error.code != "TASK_IN_PROGRESS":
                    raise
        time.sleep(30)
