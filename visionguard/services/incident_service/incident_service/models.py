from __future__ import annotations

OPEN, UNDER_REVIEW, CONFIRMED, FALSE_POSITIVE, RESOLVED = (
    "OPEN", "UNDER_REVIEW", "CONFIRMED", "FALSE_POSITIVE", "RESOLVED")
ACTIVE = (OPEN, UNDER_REVIEW)            # states in which new events may be merged into an incident

# action -> (allowed source states, target state)
ACTIONS = {
    "start_review": ({OPEN}, UNDER_REVIEW),
    "confirm": ({OPEN, UNDER_REVIEW}, CONFIRMED),
    "reject": ({OPEN, UNDER_REVIEW}, FALSE_POSITIVE),
    "resolve": ({CONFIRMED}, RESOLVED),
}

TOPIC_CREATED = "incident.created.v1"
TOPIC_UPDATED = "incident.updated.v1"
TOPIC_STATUS = "incident.status_changed.v1"
SCHEMA_VERSION = "1.0"


class IncidentError(Exception):
    pass


class NotFound(IncidentError):
    pass


class InvalidTransition(IncidentError):
    pass


class InvalidEvent(IncidentError):
    pass


REQUIRED_EVENT_FIELDS = ("event_id", "idempotency_key", "event_type", "camera_id", "zone", "timestamp")


def validate_event(ev: dict) -> None:
    missing = [k for k in REQUIRED_EVENT_FIELDS if not ev.get(k)]
    if missing:
        raise InvalidEvent(f"event missing fields: {missing}")
    if not str(ev.get("schema_version", "1")).startswith("1"):
        raise InvalidEvent(f"unsupported schema_version {ev.get('schema_version')!r}")
