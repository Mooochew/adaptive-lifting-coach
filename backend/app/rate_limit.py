"""In-memory limits on AI reviews: per client (sliding window) and a global daily cap.

Each review can cost a few paid model calls, so a public URL must not be an open tap. This keeps
the counters in the server process: fine for one instance and a class demo; a multi-instance
deployment would need a shared store (e.g. Redis) or the host's rate limiting. Also set a monthly
spend limit in the provider's dashboard as the final backstop.

Clients are keyed by the address uvicorn reports. Uvicorn rewrites it from X-Forwarded-For only when
the request comes from an address in FORWARDED_ALLOW_IPS (default 127.0.0.1, i.e. a proxy or tunnel
on the same machine, which append the real client address). So:
  - directly exposed: the connection's IP; a client can't spoof it with headers
  - behind a hosting proxy on another machine: every user shares the proxy's IP (one bucket for the
    whole site) unless FORWARDED_ALLOW_IPS lists the proxy's address
  - never set FORWARDED_ALLOW_IPS='*' on a public server: then anyone can pick their own bucket
The daily cap resets at midnight UTC.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

DAY_SECONDS = 86_400


class RateLimiter:
    def __init__(self, *, per_window: int, window_s: float, per_day: int,
                 clock: Callable[[], float] = time.time):
        self.per_window = per_window
        self.window_s = window_s
        self.per_day = per_day
        self.clock = clock
        self._lock = threading.Lock()
        self._recent: dict[str, deque[float]] = {}
        self._day = -1
        self.used_today = 0

    def allow(self, client: str) -> bool:
        """Record and allow one review for `client`, or refuse (nothing is recorded when refused)."""
        now = self.clock()
        with self._lock:
            day = int(now // DAY_SECONDS)
            if day != self._day:
                self._day, self.used_today = day, 0
            for key in list(self._recent):          # forget clients idle for a whole window
                stamps = self._recent[key]
                while stamps and now - stamps[0] >= self.window_s:
                    stamps.popleft()
                if not stamps:
                    del self._recent[key]
            stamps = self._recent.get(client, deque())
            if len(stamps) >= self.per_window or self.used_today >= self.per_day:
                return False
            stamps.append(now)
            self._recent[client] = stamps
            self.used_today += 1
            return True
