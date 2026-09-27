# SMTPing for Python

Official Python SDK for the [SMTPing](https://smtping.com) email verification API.

- Sync and async clients, Python 3.8+
- Type hints included
- Automatic retries on rate limits (429) and server errors (5xx)
- Bulk jobs up to 100,000 addresses, with polling built in

## Install

```bash
pip install smtping
```

Create an API key in the [SMTPing dashboard](https://app.smtping.com). Pass it to the client or set `SMTPING_API_KEY`.

## Verify one address

```python
from smtping import Smtping

client = Smtping()  # reads SMTPING_API_KEY, or Smtping("sk_live_...")

r = client.verify("jane@example.com")
print(r["status"], r["band"])  # valid safe
```

Every result carries a `band` field for simple routing:

| band | statuses | action |
| --- | --- | --- |
| `safe` | valid, alias | send |
| `avoid` | invalid, spamtrap, disposable, blacklisted, complainer, spambot, inbox_full | remove |
| `judgement` | catch_all, unknown, role and others | your call |

## Verify a list

Small lists with parallel single calls:

```python
rows = client.verify_many(["a@example.com", "b@example.com"], concurrency=5)
```

Large lists as one bulk job:

```python
job = client.bulk.create(emails)            # {"jobId": ..., "status": ...}
rows = client.bulk.wait(job["jobId"], on_progress=lambda s: print(s.get("processedEmails")))

# or in one call
rows = client.bulk.run(emails)
sendable = [r for r in rows if r["band"] == "safe"]
```

Check a job later with `client.bulk.get(job_id)` and `client.bulk.results(job_id)`.

## Threat list checks

```python
client.check("spamtrap", "jane@example.com")
# also: "disposable", "spambot", "complainer"
```

## Credits

```python
print(client.credits()["remaining"])
```

## Async

```python
import asyncio
from smtping import AsyncSmtping

async def main():
    async with AsyncSmtping() as client:
        r = await client.verify("jane@example.com")
        rows = await client.bulk.run(["a@example.com", "b@example.com"])

asyncio.run(main())
```

## Errors

```python
from smtping import InsufficientCreditsError, RateLimitError, SmtpingError

try:
    client.verify("jane@example.com")
except InsufficientCreditsError:
    ...  # top up
except SmtpingError as e:
    print(e.status, e.message)
```

Classes: `SmtpingError` (base, with `status` and `body`), `AuthenticationError`, `InsufficientCreditsError`, `RateLimitError`, `ValidationError`, `JobFailedError`, `TimeoutError`.

## Options

| option | default | |
| --- | --- | --- |
| `api_key` | `SMTPING_API_KEY` | required |
| `base_url` | `https://api.smtping.com/api/v1` | |
| `timeout` | `60.0` | seconds per request |
| `max_retries` | `3` | network errors, 429, 5xx |

## Command line

A CLI ships with the Node package: `npx @smtping/sdk verify jane@example.com`. See the [docs](https://smtping.com/docs#sdks).

## Links

- [API documentation](https://smtping.com/docs)
- [Pricing](https://smtping.com/pricing)
- Support: support@smtping.com

MIT License
