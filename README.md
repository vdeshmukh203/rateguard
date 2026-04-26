# rateguard

Local rate limiter for LLM API calls. Provides a token-bucket and a
sliding-window primitive, both thread-safe, both pure standard library.

## Install

```bash
pip install rateguard
```

## Usage

```python
from rateguard import TokenBucket, SlidingWindow

# Token bucket: 10 requests/sec, burst up to 20.
bucket = TokenBucket(rate=10.0, burst=20)
wait = bucket.acquire(1)
if wait > 0:
    import time; time.sleep(wait)
# proceed with the API call

# Sliding window: at most 60 calls per minute.
window = SlidingWindow(max_calls=60, window_seconds=60.0)
wait = window.acquire()
```

## License

MIT - see LICENSE.
