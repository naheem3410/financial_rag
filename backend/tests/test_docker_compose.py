"""Checks the compose file's own shape (fast, no Docker needed). Known issue 3, decision 16.

The real "boxes restart by themselves" behaviour needs a live Docker
daemon, so it is exercised by hand (docker restart + docker ps), not here.
"""
from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parent.parent / "docker-compose.yml"


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_compose_file_exists_and_parses():
    compose = _load_compose()

    assert "services" in compose


def test_all_three_boxes_are_present():
    services = _load_compose()["services"]

    assert set(services) == {"postgres", "mongo", "redis"}


def test_every_service_restarts_by_itself():
    services = _load_compose()["services"]

    for name, service in services.items():
        assert service.get("restart") == "unless-stopped", f"{name} is missing the restart policy"


def test_every_service_has_a_healthcheck():
    services = _load_compose()["services"]

    for name, service in services.items():
        assert "healthcheck" in service, f"{name} has no healthcheck"


def test_postgres_matches_the_existing_dev_container_credentials():
    """These must match the DSN in api/server.py and README.md, or a fresh
    setup would connect with the wrong password."""
    postgres = _load_compose()["services"]["postgres"]

    assert postgres["environment"]["POSTGRES_USER"] == "financial_rag"
    assert postgres["environment"]["POSTGRES_PASSWORD"] == "financial_rag_dev"


def test_every_service_keeps_its_data_in_a_named_volume():
    compose = _load_compose()

    volume_names = set(compose.get("volumes", {}))
    for name, service in compose["services"].items():
        mounted = {v.split(":")[0] for v in service.get("volumes", [])}
        assert mounted & volume_names, f"{name} has no named (persistent) volume"
