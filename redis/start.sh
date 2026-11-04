#!/bin/sh
set -eu
# Redis и Sentinel переписывают конфигурацию при смене ролей.
if [ ! -f /data/redis.conf ]; then
    cp /config/redis.conf /data/redis.conf
fi
exec redis-server /data/redis.conf "$@"
