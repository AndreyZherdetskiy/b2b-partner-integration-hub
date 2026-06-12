#!/usr/bin/env bash
# Idempotent Stage 1 topic bootstrap — spec §7.2.
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-kafka:19092}"
KAFKA_TOPICS="/opt/kafka/bin/kafka-topics.sh"

TOPICS=(
  hub.outbound.pending
  hub.outbound.retry.30s
  hub.outbound.retry.1m
  hub.outbound.retry.5m
  hub.outbound.retry.15m
  hub.outbound.retry.1h
  hub.outbound.dlq
  hub.inbound.order.created
  hub.inbound.order.updated
  hub.integration.sla_breached
)

for topic in "${TOPICS[@]}"; do
  echo "Ensuring topic: ${topic}"
  "${KAFKA_TOPICS}" \
    --bootstrap-server "${BOOTSTRAP}" \
    --create \
    --if-not-exists \
    --topic "${topic}" \
    --partitions 1 \
    --replication-factor 1
done

echo "Kafka topics ready."
