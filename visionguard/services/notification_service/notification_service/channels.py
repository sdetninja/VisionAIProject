"""Channels. send(msg) raises on failure. Secrets come from env vars, never from config files."""
from __future__ import annotations
import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from pathlib import Path


class ConsoleChannel:
    def __init__(self, **_):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        print(f"[{msg['severity']}] {msg['title']}\n  {msg['text']}")


class JsonlChannel:
    def __init__(self, path="data/notifications.jsonl", **_):
        self.p = Path(path)
        self.p.parent.mkdir(parents=True, exist_ok=True)

    def send(self, msg):
        with open(self.p, "a") as f:
            f.write(json.dumps(msg) + "\n")


def _post(url, body: dict, timeout=5):
    if not url.startswith(("https://", "http://")):
        raise ValueError("bad webhook url")
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status >= 300:
            raise RuntimeError(f"HTTP {r.status}")


class SlackChannel:  # pragma: no cover - network
    def __init__(self, webhook_url_env="SLACK_WEBHOOK_URL", **_):
        self.env = webhook_url_env

    def send(self, msg):
        url = os.environ.get(self.env)
        if not url:
            raise RuntimeError(f"env {self.env} not set")
        icon = {"CRITICAL": ":rotating_light:", "HIGH": ":warning:"}.get(msg["severity"], ":information_source:")
        text = f"{icon} *{msg['title']}*\n{msg['text']}" + (f"\n<{msg['link']}|Open incident>" if msg.get("link") else "")
        _post(url, {"text": text})


class WebhookChannel:  # pragma: no cover - network
    def __init__(self, url_env="NOTIFY_WEBHOOK_URL", **_):
        self.env = url_env

    def send(self, msg):
        url = os.environ.get(self.env)
        if not url:
            raise RuntimeError(f"env {self.env} not set")
        _post(url, msg)


class EmailChannel:  # pragma: no cover - network
    def __init__(self, host, to, port=587, sender="visionguard@localhost", user_env="SMTP_USER", pass_env="SMTP_PASS", starttls=True, **_):
        self.host, self.port, self.to, self.sender = host, port, to, sender
        self.user_env, self.pass_env, self.starttls = user_env, pass_env, starttls

    def send(self, msg):
        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = f"[{msg['severity']}] {msg['title']}", self.sender, ", ".join(self.to)
        m.set_content(msg["text"] + (f"\n\n{msg['link']}" if msg.get("link") else ""))
        with smtplib.SMTP(self.host, self.port, timeout=10) as s:
            if self.starttls:
                s.starttls()
            u, p = os.environ.get(self.user_env), os.environ.get(self.pass_env)
            if u and p:
                s.login(u, p)
            s.send_message(m)


TYPES = {"console": ConsoleChannel, "jsonl": JsonlChannel, "slack": SlackChannel, "webhook": WebhookChannel, "email": EmailChannel}


def build_channels(spec: dict) -> dict:
    out = {}
    for name, c in spec.items():
        c = dict(c)
        t = c.pop("type")
        if t not in TYPES:
            raise ValueError(f"unknown channel type {t!r}")
        out[name] = TYPES[t](**c)
    return out
