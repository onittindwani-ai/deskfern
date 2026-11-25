"""Sign in throttling. Three limits, and a short memory of who the real owner is.

1. Per address: every try from one address, whatever email it names.
2. Per address and account: tries at one account from one address (a burst, then one more every 12 seconds).
3. Per account, from anywhere, counting only FAILED tries (a burst, then one more every 30 seconds). It stops guesses that are spread over many
   addresses. An attempt from an address that already signed in successfully as that account in the last 24 hours is never held up by it, so
   someone who only knows the owner's email cannot keep the owner out by failing on purpose from somewhere else.

Residual risk: while an attack on the account is running, a first sign in from a NEW address (a new phone, a new office) waits until the failures
slow down. The per address, and per address and account, limits still apply to everyone, including a known address. The memory is in this process only: a restart
forgets it, and behind a proxy that does not pass the visitor's address on, every visitor looks like the same address.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from sendora_core.ratelimit import RateLimitExceeded, TokenBucket

KNOWN_FOR_S = 24 * 3600
MAX_KNOWN = 2048
MAX_KEYS = 10_000


class _Failures:
    """A bucket that is only spent on a failure. `blocked` looks without spending."""

    def __init__(self, capacity: int, refill_per_sec: float, clock: Callable[[], float]):
        self.capacity, self.refill, self.clock = capacity, refill_per_sec, clock
        self._t: dict[str, tuple[float, float]] = {}

    def _tokens(self, key: str, now: float) -> float:
        tokens, last = self._t.get(key, (float(self.capacity), now))
        return min(float(self.capacity), tokens + (now - last) * self.refill)

    def blocked(self, key: str) -> float | None:
        """Seconds to wait, or None when the account may be tried."""
        tokens = self._tokens(key[:256], self.clock())
        return None if tokens >= 1 else (1 - tokens) / self.refill

    def spend(self, key: str) -> None:
        now, key = self.clock(), key[:256]
        if key not in self._t and len(self._t) >= MAX_KEYS:
            full = [k for k in self._t if self._tokens(k, now) >= self.capacity]
            for k in full or sorted(self._t, key=lambda k: self._t[k][1])[: MAX_KEYS // 10]:
                del self._t[k]
        self._t[key] = (max(0.0, self._tokens(key, now) - 1), now)


class LoginGuard:
    def __init__(self, burst: int, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self._ip = TokenBucket(capacity=max(30, burst * 6), refill_per_sec=1 / 3, clock=clock)
        self._pair = TokenBucket(capacity=burst, refill_per_sec=1 / 12, clock=clock)
        self._failures = _Failures(max(20, burst * 4), 1 / 30, clock)
        self._known: OrderedDict[str, float] = OrderedDict()

    @staticmethod
    def _known_key(host: str, email: str) -> str:
        return hashlib.sha256(f"{host}|{email}".encode()).hexdigest()  # no address or email is kept in the clear

    def is_known(self, host: str, email: str) -> bool:
        key, now = self._known_key(host, email), self.clock()
        until = self._known.get(key)
        if until is None:
            return False
        if until <= now:
            del self._known[key]
            return False
        return True

    def check(self, host: str, email: str) -> None:
        """Raises RateLimitExceeded when this try must wait. Call before the password is looked at."""
        with self._lock:
            self._ip.check(host)
            self._pair.check(f"{host}:{email}")
            wait = None if self.is_known(host, email) else self._failures.blocked(email)
            if wait is not None:
                raise RateLimitExceeded(wait)

    def failed(self, host: str, email: str) -> None:
        with self._lock:
            self._failures.spend(email)

    def succeeded(self, host: str, email: str) -> None:
        """Remember that this address signed in as this account. Set only after the password was verified."""
        with self._lock:
            key = self._known_key(host, email)
            self._known.pop(key, None)
            self._known[key] = self.clock() + KNOWN_FOR_S
            while len(self._known) > MAX_KNOWN:
                self._known.popitem(last=False)
