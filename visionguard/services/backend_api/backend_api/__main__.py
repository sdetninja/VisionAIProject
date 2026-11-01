"""Dev helper: python -m backend_api token --sub alice --role reviewer [--cameras CAM_001 CAM_002]"""
import argparse, os, time
from .auth import HS256Verifier
from .config import Settings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["token"])
    ap.add_argument("--sub", required=True); ap.add_argument("--role", required=True)
    ap.add_argument("--cameras", nargs="*"); ap.add_argument("--hours", type=float, default=8)
    a = ap.parse_args()
    s = Settings.from_env()
    v = HS256Verifier(os.environ[s.jwt_secret_env], s.audience, s.issuer)
    c = {"sub": a.sub, "role": a.role, "aud": s.audience, "iss": s.issuer, "exp": time.time() + a.hours * 3600}
    if a.cameras is not None:
        c["cameras"] = a.cameras
    print(v.sign(c))


if __name__ == "__main__":
    main()
