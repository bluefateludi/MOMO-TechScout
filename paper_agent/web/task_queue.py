from __future__ import annotations

import secrets
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Protocol


class QueueFullError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Lease:
    run_id: str
    token: str
    worker_id: str
    expires_at: datetime


class RunQueue(Protocol):
    def enqueue(self, run_id: str, *, now: datetime | None = None) -> bool: ...
    def reserve(
        self, worker_id: str, *, lease_seconds: int, now: datetime | None = None,
    ) -> Lease | None: ...
    def heartbeat(
        self, lease: Lease, *, lease_seconds: int, now: datetime | None = None,
    ) -> bool: ...
    def ack(self, lease: Lease) -> bool: ...
    def retry(self, lease: Lease) -> bool: ...
    def dead_letter(self, lease: Lease, *, reason: str) -> bool: ...
    def reap_expired(self, *, now: datetime | None = None) -> list[Lease]: ...
    def discard(self, run_id: str) -> None: ...
    def dead_letter_run(self, run_id: str, *, reason: str) -> None: ...
    def allow(self, subject: str, *, now: datetime | None = None) -> bool: ...
    def ready(self) -> bool: ...


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(timezone.utc)


class InMemoryRunQueue:
    """Deterministic queue adapter used by local composition and tests."""

    def __init__(
        self,
        *,
        capacity: int,
        rate_limit: int = 30,
        rate_window_seconds: int = 60,
    ) -> None:
        self.capacity = capacity
        self.rate_limit = rate_limit
        self.rate_window_seconds = rate_window_seconds
        self._pending: deque[str] = deque()
        self._known: set[str] = set()
        self._leases: dict[str, Lease] = {}
        self._rate: dict[str, deque[datetime]] = {}
        self._dead: list[tuple[str, str]] = []
        self._lock = Lock()

    def enqueue(self, run_id: str, *, now: datetime | None = None) -> bool:
        del now
        with self._lock:
            if run_id in self._known:
                return False
            if len(self._known) >= self.capacity:
                raise QueueFullError("queue capacity reached")
            self._known.add(run_id)
            self._pending.append(run_id)
            return True

    def reserve(
        self, worker_id: str, *, lease_seconds: int, now: datetime | None = None,
    ) -> Lease | None:
        current = _now(now)
        with self._lock:
            if not self._pending:
                return None
            run_id = self._pending.popleft()
            lease = Lease(
                run_id, secrets.token_urlsafe(18), worker_id,
                current + timedelta(seconds=lease_seconds),
            )
            self._leases[run_id] = lease
            return lease

    def heartbeat(
        self, lease: Lease, *, lease_seconds: int, now: datetime | None = None,
    ) -> bool:
        current = _now(now)
        with self._lock:
            stored = self._leases.get(lease.run_id)
            if stored is None or stored.token != lease.token:
                return False
            self._leases[lease.run_id] = Lease(
                lease.run_id, lease.token, lease.worker_id,
                current + timedelta(seconds=lease_seconds),
            )
            return True

    def ack(self, lease: Lease) -> bool:
        with self._lock:
            if not self._owns(lease):
                return False
            self._leases.pop(lease.run_id, None)
            self._known.discard(lease.run_id)
            return True

    def retry(self, lease: Lease) -> bool:
        with self._lock:
            if not self._owns(lease):
                return False
            self._leases.pop(lease.run_id, None)
            self._pending.append(lease.run_id)
            return True

    def dead_letter(self, lease: Lease, *, reason: str) -> bool:
        with self._lock:
            if not self._owns(lease):
                return False
            self._leases.pop(lease.run_id, None)
            self._known.discard(lease.run_id)
            self._dead.append((lease.run_id, reason))
            return True

    def reap_expired(self, *, now: datetime | None = None) -> list[Lease]:
        current = _now(now)
        with self._lock:
            expired = sorted(
                (lease for lease in self._leases.values() if lease.expires_at <= current),
                key=lambda lease: (lease.expires_at, lease.run_id),
            )
            for lease in expired:
                self._leases.pop(lease.run_id, None)
                self._pending.append(lease.run_id)
            return expired

    def discard(self, run_id: str) -> None:
        with self._lock:
            self._pending = deque(item for item in self._pending if item != run_id)
            self._leases.pop(run_id, None)
            self._known.discard(run_id)

    def dead_letter_run(self, run_id: str, *, reason: str) -> None:
        self.discard(run_id)
        with self._lock:
            if (run_id, reason) not in self._dead:
                self._dead.append((run_id, reason))

    def allow(self, subject: str, *, now: datetime | None = None) -> bool:
        current = _now(now)
        cutoff = current - timedelta(seconds=self.rate_window_seconds)
        with self._lock:
            history = self._rate.setdefault(subject, deque())
            while history and history[0] <= cutoff:
                history.popleft()
            if len(history) >= self.rate_limit:
                return False
            history.append(current)
            return True

    def ready(self) -> bool:
        return True

    def dead_letters(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._dead)

    def _owns(self, lease: Lease) -> bool:
        stored = self._leases.get(lease.run_id)
        return stored is not None and stored.token == lease.token


class RedisRunQueue:
    """Redis queue/lease/rate-limit adapter. Run state never lives here."""

    def __init__(
        self,
        client: object,
        *,
        namespace: str = "momo:techscout",
        capacity: int = 100,
        rate_limit: int = 30,
        rate_window_seconds: int = 60,
    ) -> None:
        self.client = client
        self.namespace = namespace.rstrip(":")
        self.capacity = capacity
        self.rate_limit = rate_limit
        self.rate_window_seconds = rate_window_seconds

    @classmethod
    def from_url(cls, url: str, **kwargs: object) -> "RedisRunQueue":
        try:
            from redis import Redis
        except ImportError as exc:
            raise RuntimeError("Redis worker mode requires the 'redis' package") from exc
        return cls(
            Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                retry_on_timeout=False,
                health_check_interval=15,
            ),
            **kwargs,
        )

    def _key(self, suffix: str) -> str:
        return f"{self.namespace}:{suffix}"

    def enqueue(self, run_id: str, *, now: datetime | None = None) -> bool:
        del now
        result = self.client.eval(
            """
            if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 1 then return 0 end
            if redis.call('SCARD', KEYS[1]) >= tonumber(ARGV[2]) then return -1 end
            redis.call('SADD', KEYS[1], ARGV[1])
            redis.call('LPUSH', KEYS[2], ARGV[1])
            return 1
            """,
            2, self._key("known"), self._key("pending"), run_id, self.capacity,
        )
        if int(result) < 0:
            raise QueueFullError("queue capacity reached")
        return bool(result)

    def reserve(
        self, worker_id: str, *, lease_seconds: int, now: datetime | None = None,
    ) -> Lease | None:
        current = _now(now)
        token = secrets.token_urlsafe(18)
        expires_at = current + timedelta(seconds=lease_seconds)
        run_id = self.client.eval(
            """
            local id = redis.call('RPOP', KEYS[1])
            if not id then return false end
            redis.call('RPUSH', KEYS[2], id)
            redis.call('HSET', KEYS[3], id, ARGV[1])
            redis.call('HSET', KEYS[4], id, ARGV[2])
            redis.call('ZADD', KEYS[5], ARGV[3], id)
            return id
            """,
            5,
            self._key("pending"), self._key("processing"), self._key("tokens"),
            self._key("workers"), self._key("leases"),
            token, worker_id, int(expires_at.timestamp() * 1000),
        )
        if not run_id:
            return None
        return Lease(str(run_id), token, worker_id, expires_at)

    def heartbeat(
        self, lease: Lease, *, lease_seconds: int, now: datetime | None = None,
    ) -> bool:
        expires_at = _now(now) + timedelta(seconds=lease_seconds)
        result = self.client.eval(
            """
            if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then return 0 end
            redis.call('ZADD', KEYS[2], ARGV[3], ARGV[1])
            return 1
            """,
            2, self._key("tokens"), self._key("leases"),
            lease.run_id, lease.token, int(expires_at.timestamp() * 1000),
        )
        return bool(result)

    def ack(self, lease: Lease) -> bool:
        return self._finish(lease, action="ack")

    def retry(self, lease: Lease) -> bool:
        return self._finish(lease, action="retry")

    def dead_letter(self, lease: Lease, *, reason: str) -> bool:
        result = self.client.eval(
            """
            if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then return 0 end
            redis.call('LREM', KEYS[2], 1, ARGV[1])
            redis.call('ZREM', KEYS[3], ARGV[1])
            redis.call('HDEL', KEYS[1], ARGV[1])
            redis.call('HDEL', KEYS[4], ARGV[1])
            redis.call('SREM', KEYS[5], ARGV[1])
            redis.call('RPUSH', KEYS[6], ARGV[1])
            redis.call('HSET', KEYS[7], ARGV[1], ARGV[3])
            return 1
            """,
            7,
            self._key("tokens"), self._key("processing"), self._key("leases"),
            self._key("workers"), self._key("known"), self._key("dead"),
            self._key("dead_reasons"), lease.run_id, lease.token, reason,
        )
        return bool(result)

    def reap_expired(self, *, now: datetime | None = None) -> list[Lease]:
        current_ms = int(_now(now).timestamp() * 1000)
        candidates = self.client.zrangebyscore(self._key("leases"), "-inf", current_ms)
        requeued: list[Lease] = []
        for run_id in candidates:
            token = self.client.hget(self._key("tokens"), run_id)
            worker_id = self.client.hget(self._key("workers"), run_id)
            score = self.client.zscore(self._key("leases"), run_id)
            moved = self.client.eval(
                """
                local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
                if not score or tonumber(score) > tonumber(ARGV[2]) then return 0 end
                redis.call('LREM', KEYS[2], 1, ARGV[1])
                redis.call('ZREM', KEYS[1], ARGV[1])
                redis.call('HDEL', KEYS[3], ARGV[1])
                redis.call('HDEL', KEYS[4], ARGV[1])
                redis.call('LPUSH', KEYS[5], ARGV[1])
                return 1
                """,
                5,
                self._key("leases"), self._key("processing"), self._key("tokens"),
                self._key("workers"), self._key("pending"), run_id, current_ms,
            )
            if moved and token and worker_id and score is not None:
                requeued.append(Lease(
                    str(run_id), str(token), str(worker_id),
                    datetime.fromtimestamp(float(score) / 1000, tz=timezone.utc),
                ))
        return requeued

    def discard(self, run_id: str) -> None:
        self.client.eval(
            """
            redis.call('LREM', KEYS[1], 0, ARGV[1])
            redis.call('LREM', KEYS[2], 0, ARGV[1])
            redis.call('ZREM', KEYS[3], ARGV[1])
            redis.call('HDEL', KEYS[4], ARGV[1])
            redis.call('HDEL', KEYS[5], ARGV[1])
            redis.call('SREM', KEYS[6], ARGV[1])
            return 1
            """,
            6,
            self._key("pending"), self._key("processing"), self._key("leases"),
            self._key("tokens"), self._key("workers"), self._key("known"), run_id,
        )

    def dead_letter_run(self, run_id: str, *, reason: str) -> None:
        self.discard(run_id)
        self.client.eval(
            """
            redis.call('RPUSH', KEYS[1], ARGV[1])
            redis.call('HSET', KEYS[2], ARGV[1], ARGV[2])
            return 1
            """,
            2, self._key("dead"), self._key("dead_reasons"), run_id, reason,
        )

    def allow(self, subject: str, *, now: datetime | None = None) -> bool:
        current_ms = int(_now(now).timestamp() * 1000)
        window_ms = self.rate_window_seconds * 1000
        member = f"{current_ms}:{secrets.token_hex(6)}"
        result = self.client.eval(
            """
            redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1] - ARGV[2])
            if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
            redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
            redis.call('PEXPIRE', KEYS[1], ARGV[2])
            return 1
            """,
            1, self._key(f"rate:{subject}"),
            current_ms, window_ms, self.rate_limit, member,
        )
        return bool(result)

    def ready(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def _finish(self, lease: Lease, *, action: str) -> bool:
        result = self.client.eval(
            """
            if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then return 0 end
            redis.call('LREM', KEYS[2], 1, ARGV[1])
            redis.call('ZREM', KEYS[3], ARGV[1])
            redis.call('HDEL', KEYS[1], ARGV[1])
            redis.call('HDEL', KEYS[4], ARGV[1])
            if ARGV[3] == 'retry' then
              redis.call('LPUSH', KEYS[5], ARGV[1])
            else
              redis.call('SREM', KEYS[6], ARGV[1])
            end
            return 1
            """,
            6,
            self._key("tokens"), self._key("processing"), self._key("leases"),
            self._key("workers"), self._key("pending"), self._key("known"),
            lease.run_id, lease.token, action,
        )
        return bool(result)
