FROM busybox:1.36.1-uclibc AS busybox

FROM otel/opentelemetry-collector-contrib:0.109.0

COPY --from=busybox /bin/busybox /bin/busybox
COPY collector.yaml /etc/otelcol/config.yaml
