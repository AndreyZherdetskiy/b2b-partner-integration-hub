"""Locust scenarios for Partner Integration Hub smoke and load runs."""

from __future__ import annotations

import os
import time

import uuid6
from locust import HttpUser, between, constant, events, task

from loadtests.config import admin_token, load_wait_bounds
from loadtests.preflight import PreflightError, assert_minimum_requests, resolve_partner_public_id

_min_wait, _max_wait = load_wait_bounds()
_wait_time = constant(0) if _min_wait <= 0 and _max_wait <= 0 else between(_min_wait, _max_wait)


class HubOutboundUser(HttpUser):
    wait_time = _wait_time

    def on_start(self) -> None:
        self.token = admin_token()
        if not self.token:
            raise PreflightError(
                "ADMIN_BOOTSTRAP_TOKEN or LOAD_ADMIN_TOKEN must be set in process environment"
            )

        partner_id = os.environ.get("LOAD_PARTNER_PUBLIC_ID", "").strip()
        if partner_id:
            self.partner_id = partner_id
        else:
            self.partner_id = resolve_partner_public_id(token=self.token)

    @task(3)
    def accept_outbound_event(self) -> None:
        idem = f"locust-{id(self)}-{time.monotonic_ns()}"
        corr = str(uuid6.uuid7())
        body = {
            "partner_id": self.partner_id,
            "event_type": "order.created",
            "payload": {"order_id": f"locust-{idem}", "amount": 1.0},
            "idempotency_key": idem,
            "correlation_id": corr,
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-Correlation-Id": corr,
        }
        with self.client.post(
            "/internal/v1/outbound/events",
            json=body,
            headers=headers,
            name="/internal/v1/outbound/events",
            catch_response=True,
        ) as response:
            if response.status_code != 202:
                response.failure(f"expected 202, got {response.status_code}")
                return
            try:
                data = response.json()
            except ValueError:
                response.failure("invalid JSON response")
                return
            if data.get("status") != "accepted":
                response.failure(f"expected status=accepted, got {data.get('status')!r}")

    @task(1)
    def inbound_health(self) -> None:
        with self.client.get(
            "/inbound/v1/health",
            name="/inbound/v1/health",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}")


@events.quitting.add_listener
def _assert_minimum_requests_on_quit(environment: events.Environment, **_kwargs: object) -> None:
    try:
        assert_minimum_requests(request_count=environment.stats.total.num_requests)
    except PreflightError:
        environment.process_exit_code = 1
