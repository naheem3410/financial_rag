"""A worked example of a listener, used by the test that checks listeners
stay free of any bus import. A real listener (ingestion, labelling,
progress) looks just like this: a plain function, nothing about the bus.
"""
from __future__ import annotations


def handle_document_uploaded(event) -> str:
    """`event` only needs `.type`, `.job_id`, `.data` -- duck typing, no import."""
    return f"would ingest job {event.job_id}"
