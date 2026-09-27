import asyncio
import os
import random
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import quote

import httpx

from ._errors import (
    AuthenticationError,
    JobFailedError,
    SmtpingError,
    TimeoutError,
    ValidationError,
    error_for,
)

VERSION = "1.0.0"
DEFAULT_BASE_URL = "https://api.smtping.com/api/v1"
SAFE = ("valid", "alias")
AVOID = ("invalid", "spamtrap", "disposable", "blacklisted", "complainer", "spambot", "inbox_full")
CHECKS = ("spamtrap", "disposable", "spambot", "complainer")
BULK_MAX = 100_000
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Result = Dict[str, Any]


def band(status: str) -> str:
    """Maps a status to 'safe', 'avoid' or 'judgement'."""
    if status in SAFE:
        return "safe"
    if status in AVOID:
        return "avoid"
    return "judgement"


def is_email(value: str) -> bool:
    return bool(_EMAIL.match(str(value or "").strip()))


def _with_band(r: Any) -> Any:
    if isinstance(r, dict) and isinstance(r.get("status"), str) and "band" not in r:
        r = {**r, "band": band(r["status"])}
    return r


def _clean_list(emails: Iterable[str]) -> List[str]:
    seen: Dict[str, None] = {}
    for e in emails:
        e = str(e or "").strip().lower()
        if is_email(e):
            seen.setdefault(e, None)
    out = list(seen)
    if not out:
        raise ValidationError("No valid email address in the list")
    if len(out) > BULK_MAX:
        raise ValidationError(f"A bulk job accepts up to {BULK_MAX} addresses")
    return out


def _backoff(attempt: int) -> float:
    return min(2 ** (attempt - 1), 15) + random.random() * 0.25


def _parse(res: httpx.Response) -> Any:
    try:
        return res.json() if res.content else None
    except ValueError:
        return {"raw": res.text}


def _retry_after(res: httpx.Response, attempt: int) -> float:
    try:
        v = float(res.headers.get("retry-after", ""))
        if v > 0:
            return v
    except ValueError:
        pass
    return _backoff(attempt)


def _results_list(r: Any) -> List[Result]:
    rows = r if isinstance(r, list) else (r or {}).get("results", []) if isinstance(r, dict) else []
    return [_with_band(x) for x in rows]


def _job_error(job_id: str, st: dict) -> JobFailedError:
    msg = f"Job {job_id} {str(st.get('status')).lower()}"
    if st.get("errorMessage"):
        msg += f": {st['errorMessage']}"
    return JobFailedError(msg, st)


class _Base:
    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or os.environ.get("SMTPING_API_KEY", "")
        if not self.api_key:
            raise AuthenticationError("Missing API key. Pass api_key or set SMTPING_API_KEY.")
        self.base_url = (base_url or os.environ.get("SMTPING_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": f"smtping-python/{VERSION}",
        }


class Smtping(_Base):
    """Synchronous client.

    >>> from smtping import Smtping
    >>> client = Smtping()  # reads SMTPING_API_KEY
    >>> client.verify("someone@example.com")["band"]
    """

    def __init__(self, api_key: Optional[str] = None, **kw: Any) -> None:
        super().__init__(api_key, **kw)
        self._http = httpx.Client(base_url=self.base_url, headers=self._headers, timeout=self.timeout)
        self.bulk = _Bulk(self)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Smtping":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def request(self, method: str, path: str, json: Any = None) -> Any:
        attempt = 0
        while True:
            try:
                res = self._http.request(method, path, json=json)
            except httpx.TimeoutException:
                attempt += 1
                if attempt <= self.max_retries:
                    time.sleep(_backoff(attempt))
                    continue
                raise TimeoutError(f"Request timed out after {self.timeout} s")
            except httpx.HTTPError as e:
                attempt += 1
                if attempt <= self.max_retries:
                    time.sleep(_backoff(attempt))
                    continue
                raise SmtpingError(f"Network error: {e}")
            if res.is_success:
                return _parse(res)
            if (res.status_code == 429 or res.status_code >= 500) and attempt < self.max_retries:
                attempt += 1
                time.sleep(_retry_after(res, attempt))
                continue
            raise error_for(res.status_code, _parse(res))

    def verify(self, email: str) -> Result:
        e = str(email or "").strip()
        if not e:
            raise ValidationError("Email is required")
        return _with_band(self.request("POST", "/verify/single", {"email": e}))

    def verify_many(self, emails: Iterable[str], *, concurrency: int = 5) -> List[Result]:
        from concurrent.futures import ThreadPoolExecutor

        items = list(dict.fromkeys(str(e or "").strip().lower() for e in emails if str(e or "").strip()))

        def one(e: str) -> Result:
            try:
                return self.verify(e)
            except SmtpingError as err:
                return {"email": e, "status": "error", "statusDescription": err.message}

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            return list(pool.map(one, items))

    def check(self, check_type: str, email: str) -> Result:
        if check_type not in CHECKS:
            raise ValidationError(f"Unknown check '{check_type}'. Use one of: {', '.join(CHECKS)}")
        e = str(email or "").strip()
        r = self.request("POST", f"/checks/{check_type}", {"email": e}) or {}
        return {"email": e, "check": check_type, **r}

    def credits(self) -> Result:
        return self.request("GET", "/credits")


class _Bulk:
    def __init__(self, client: Smtping) -> None:
        self._c = client

    def create(self, emails: Iterable[str]) -> Result:
        items = _clean_list(emails)
        job = self._c.request("POST", "/verify/bulk", {"emails": items}) or {}
        job.setdefault("totalEmails", len(items))
        return job

    def get(self, job_id: str) -> Result:
        r = self._c.request("GET", f"/verify/bulk/{quote(job_id, safe='')}") or {}
        return {"jobId": job_id, **r}

    def results(self, job_id: str) -> List[Result]:
        return _results_list(self._c.request("GET", f"/verify/bulk/{quote(job_id, safe='')}/result"))

    def wait(
        self,
        job_id: str,
        *,
        timeout: float = 1800,
        interval: float = 5,
        on_progress: Optional[Callable[[Result], None]] = None,
    ) -> List[Result]:
        deadline = time.monotonic() + timeout
        delay = interval
        while True:
            st = self.get(job_id)
            if on_progress:
                on_progress(st)
            if st.get("status") == "Succeeded":
                return self.results(job_id)
            if st.get("status") in ("Failed", "Cancelled"):
                raise _job_error(job_id, st)
            if time.monotonic() + delay > deadline:
                raise TimeoutError(f"Job {job_id} still running after {int(timeout)} s")
            time.sleep(delay)
            delay = min(delay * 1.5, 30)

    def run(self, emails: Iterable[str], **kw: Any) -> List[Result]:
        return self.wait(self.create(emails)["jobId"], **kw)


class AsyncSmtping(_Base):
    """Asynchronous client with the same methods, awaited.

    >>> async with AsyncSmtping() as client:
    ...     r = await client.verify("someone@example.com")
    """

    def __init__(self, api_key: Optional[str] = None, **kw: Any) -> None:
        super().__init__(api_key, **kw)
        self._http = httpx.AsyncClient(base_url=self.base_url, headers=self._headers, timeout=self.timeout)
        self.bulk = _AsyncBulk(self)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncSmtping":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def request(self, method: str, path: str, json: Any = None) -> Any:
        attempt = 0
        while True:
            try:
                res = await self._http.request(method, path, json=json)
            except httpx.TimeoutException:
                attempt += 1
                if attempt <= self.max_retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise TimeoutError(f"Request timed out after {self.timeout} s")
            except httpx.HTTPError as e:
                attempt += 1
                if attempt <= self.max_retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise SmtpingError(f"Network error: {e}")
            if res.is_success:
                return _parse(res)
            if (res.status_code == 429 or res.status_code >= 500) and attempt < self.max_retries:
                attempt += 1
                await asyncio.sleep(_retry_after(res, attempt))
                continue
            raise error_for(res.status_code, _parse(res))

    async def verify(self, email: str) -> Result:
        e = str(email or "").strip()
        if not e:
            raise ValidationError("Email is required")
        return _with_band(await self.request("POST", "/verify/single", {"email": e}))

    async def verify_many(self, emails: Iterable[str], *, concurrency: int = 5) -> List[Result]:
        items = list(dict.fromkeys(str(e or "").strip().lower() for e in emails if str(e or "").strip()))
        sem = asyncio.Semaphore(max(1, concurrency))

        async def one(e: str) -> Result:
            async with sem:
                try:
                    return await self.verify(e)
                except SmtpingError as err:
                    return {"email": e, "status": "error", "statusDescription": err.message}

        return list(await asyncio.gather(*(one(e) for e in items)))

    async def check(self, check_type: str, email: str) -> Result:
        if check_type not in CHECKS:
            raise ValidationError(f"Unknown check '{check_type}'. Use one of: {', '.join(CHECKS)}")
        e = str(email or "").strip()
        r = await self.request("POST", f"/checks/{check_type}", {"email": e}) or {}
        return {"email": e, "check": check_type, **r}

    async def credits(self) -> Result:
        return await self.request("GET", "/credits")


class _AsyncBulk:
    def __init__(self, client: AsyncSmtping) -> None:
        self._c = client

    async def create(self, emails: Iterable[str]) -> Result:
        items = _clean_list(emails)
        job = await self._c.request("POST", "/verify/bulk", {"emails": items}) or {}
        job.setdefault("totalEmails", len(items))
        return job

    async def get(self, job_id: str) -> Result:
        r = await self._c.request("GET", f"/verify/bulk/{quote(job_id, safe='')}") or {}
        return {"jobId": job_id, **r}

    async def results(self, job_id: str) -> List[Result]:
        return _results_list(await self._c.request("GET", f"/verify/bulk/{quote(job_id, safe='')}/result"))

    async def wait(
        self,
        job_id: str,
        *,
        timeout: float = 1800,
        interval: float = 5,
        on_progress: Optional[Callable[[Result], None]] = None,
    ) -> List[Result]:
        deadline = time.monotonic() + timeout
        delay = interval
        while True:
            st = await self.get(job_id)
            if on_progress:
                on_progress(st)
            if st.get("status") == "Succeeded":
                return await self.results(job_id)
            if st.get("status") in ("Failed", "Cancelled"):
                raise _job_error(job_id, st)
            if time.monotonic() + delay > deadline:
                raise TimeoutError(f"Job {job_id} still running after {int(timeout)} s")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 30)

    async def run(self, emails: Iterable[str], **kw: Any) -> List[Result]:
        job = await self.create(emails)
        return await self.wait(job["jobId"], **kw)
