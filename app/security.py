"""Password hashing, opaque sessions, and bounded login throttling."""
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def password_valid(password, stored):
    try:
        _, salt, expected = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class RateLimiter:
    """Per-IP sliding window; one application worker is required."""
    def __init__(self):
        self.attempts = OrderedDict()

    def allow(self, key, limit=10, window=300):
        now = time.time()
        times = [t for t in self.attempts.pop(key, []) if now - t < window]
        allowed = len(times) < limit
        if allowed:
            times.append(now)
        self.attempts[key] = times
        while len(self.attempts) > 10000:
            self.attempts.popitem(last=False)
        return allowed


def create_session(store, user_id, plex=False):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    duration = 3600 if plex else 7 * 86400
    with store.connection() as db:
        db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                   (token_hash(token), user_id, csrf, time.time() + duration, time.time()))
    return token, csrf, duration
