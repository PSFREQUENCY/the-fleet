# ── pulse.py — Heartbeat + sleep cycle scheduler ─────────────────────────────
import asyncio, time, logging
from typing import Callable, Awaitable, List

log = logging.getLogger("pulse")


class Pulse:
    def __init__(self, heartbeat_sec: int = 300, sleep_sec: int = 3600):
        self.hb_sec      = heartbeat_sec
        self.sleep_sec   = sleep_sec
        self._last_hb    = 0.0
        self._last_sleep = 0.0
        self._beats      = 0
        self._cycles     = 0
        self._running    = False
        self._sleeping   = False
        self._hb_fn:    List[Callable[[], Awaitable[None]]] = []
        self._sleep_fn: List[Callable[[], Awaitable[None]]] = []

    def on_heartbeat(self, fn: Callable[[], Awaitable[None]]) -> None:
        self._hb_fn.append(fn)

    def on_sleep(self, fn: Callable[[], Awaitable[None]]) -> None:
        self._sleep_fn.append(fn)

    def sleep(self) -> None:
        self._sleeping = True
        log.info("Pulse → sleep mode (suspended)")

    def wake(self) -> None:
        self._sleeping   = False
        self._last_hb    = 0.0   # force immediate heartbeat on next tick
        log.info("Pulse → awake")

    async def start(self) -> None:
        self._running  = True
        self._sleeping = False
        log.info(f"Pulse started — HB:{self.hb_sec}s  SLEEP:{self.sleep_sec}s")
        while self._running:
            if self._sleeping:
                await asyncio.sleep(30)   # idle check only
                continue
            now = time.time()
            if now - self._last_hb >= self.hb_sec:
                self._last_hb = now
                self._beats  += 1
                for fn in self._hb_fn:
                    try:
                        await fn()
                    except Exception as e:
                        log.error(f"heartbeat: {e}")
            if now - self._last_sleep >= self.sleep_sec:
                self._last_sleep = now
                self._cycles    += 1
                for fn in self._sleep_fn:
                    try:
                        await fn()
                    except Exception as e:
                        log.error(f"sleep cycle: {e}")
            await asyncio.sleep(15)

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict:
        return {
            "beats":          self._beats,
            "sleep_cycles":   self._cycles,
            "hb_interval":    self.hb_sec,
            "sleep_interval": self.sleep_sec,
            "sleeping":       self._sleeping,
            "next_hb_in":     max(0, int(self.hb_sec - (time.time() - self._last_hb))),
            "next_sleep_in":  max(0, int(self.sleep_sec - (time.time() - self._last_sleep))),
        }
