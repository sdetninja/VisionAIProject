PERMISSIONS = {
    "viewer": {"cameras:read", "incidents:read", "assessments:read"},
    "reviewer": {"cameras:read", "incidents:read", "assessments:read", "incidents:review"},
    "admin": {"cameras:read", "incidents:read", "assessments:read", "incidents:review", "labels:read", "audit:read",
              "stream:read"},
}
# stream (live video) is deliberately admin-only until per-camera ACLs + watermarking exist.


def can(role: str, perm: str) -> bool:
    return perm in PERMISSIONS.get(role, ())


def camera_allowed(principal, camera_id) -> bool:
    return principal.cameras is None or camera_id in principal.cameras
