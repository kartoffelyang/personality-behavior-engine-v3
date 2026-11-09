# -*- coding: utf-8 -*-
"""并发竞速工具：同时发起 n 个相同请求，返回第一个成功的结果。"""
from __future__ import annotations

import queue
import threading
from typing import Callable, List, TypeVar

T = TypeVar("T")


def race_first(fn: Callable[[], T], n: int = 1) -> T:
    """并发发起 n 个相同的调用，返回第一个成功者的结果。

    - 单个失败只记录；等全部失败才抛出第一个异常（保留原有 fallback 语义）。
    - 其余仍在途的调用在后台 daemon 线程继续，不阻塞主流程。
    - n <= 1 时与直接调用 fn() 完全等价（零开销、零行为变化）。
    """
    if n <= 1:
        return fn()

    results: queue.Queue = queue.Queue()

    def run() -> None:
        try:
            results.put(("ok", fn()))
        except Exception as exc:  # noqa: BLE001 - 竞速需吞掉单个失败
            results.put(("err", exc))

    threads = [
        threading.Thread(target=run, daemon=True, name=f"llm-race-{i}") for i in range(n)
    ]
    for t in threads:
        t.start()

    errors: List[BaseException] = []
    received = 0
    while received < n:
        kind, value = results.get()
        received += 1
        if kind == "ok":
            return value
        errors.append(value)
    if errors:
        raise errors[0]
    raise RuntimeError("race_first: 所有请求均未返回")
