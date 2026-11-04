"""Cache-Aside для protobuf-ответов; поколение защищает от поздней записи старых данных."""

import logging
from threading import Lock
from uuid import uuid4

from google.protobuf.message import DecodeError
from redis.sentinel import Sentinel
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from .config import REDIS_MASTER_NAME, REDIS_SENTINELS


TTL = 300
GENERATION_KEY = "flight-cache:generation"
sentinel = Sentinel(
    REDIS_SENTINELS, socket_connect_timeout=0.5, socket_timeout=0.5,
    sentinel_kwargs={"socket_connect_timeout": 0.5, "socket_timeout": 0.5,
                     "retry": Retry(NoBackoff(), 0)},
    retry=Retry(NoBackoff(), 0),
)
client = sentinel.master_for(REDIS_MASTER_NAME)
logger = logging.getLogger(__name__)
_lock = Lock()
# После перезапуска процесса нельзя доверять кешу незавершённой инвалидации.
_dirty = True
_master_address = None


def invalidate():
    """Вызывать после COMMIT. Для небольшой БД сбрасываем всё поколение кеша."""
    global _dirty
    with _lock:
        _dirty = True
        try:
            client.set(GENERATION_KEY, uuid4().hex, ex=TTL)
            _dirty = False
            logger.info("cache invalidated")
        except RedisError:
            logger.warning("cache invalidation failed; reads will bypass cache until recovery")


def generation():
    global _dirty, _master_address
    with _lock:
        try:
            address = client.connection_pool.get_master_address()
            if address != _master_address:
                _master_address = address
                # Реплика могла не получить последнюю инвалидацию до отказа мастера.
                _dirty = True
                logger.info("Redis master: %s:%s; rotating cache generation", *address)
            if _dirty:
                client.set(GENERATION_KEY, uuid4().hex, ex=TTL)
                _dirty = False
            token = client.get(GENERATION_KEY)
            if token is None:
                # Новый случайный токен при истечении TTL не переиспользует старые ключи.
                client.set(GENERATION_KEY, uuid4().hex, ex=TTL, nx=True)
                token = client.get(GENERATION_KEY)
            return token.decode("ascii") if token else None
        except RedisError:
            logger.warning("cache unavailable; reading PostgreSQL")
            return None


def get(token, key, message_type):
    if token is None:
        return None
    try:
        data = client.get(f"{key}:{token}")
        if data is None:
            logger.info("cache miss: %s", key)
            return None
        result = message_type()
        result.ParseFromString(data)
        logger.info("cache hit: %s", key)
        return result
    except (RedisError, DecodeError):
        logger.warning("cache read failed: %s", key)
        return None


def put(token, key, message):
    if token is None:
        return
    try:
        client.set(f"{key}:{token}", message.SerializeToString(), ex=TTL)
    except RedisError:
        logger.warning("cache write failed: %s", key)
