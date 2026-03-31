FROM grafana/grafana:11.2.0

COPY infra/grafana/provisioning /etc/grafana/provisioning
COPY docs/grafana/dashboards /etc/grafana/dashboards
# Empty dirs Grafana expects; do not COPY .gitkeep into the image.
RUN mkdir -p /etc/grafana/provisioning/plugins /etc/grafana/provisioning/alerting \
    && rm -f /etc/grafana/provisioning/plugins/.gitkeep \
            /etc/grafana/provisioning/alerting/.gitkeep
