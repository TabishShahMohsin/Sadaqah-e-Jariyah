"""Shared plumbing for the GitHub Actions jobs: R2 (S3 API), the intake Worker, GitHub."""
import hashlib
import hmac
import json
import os
import subprocess

import requests


def env(name, default=None, required=True):
    v = os.environ.get(name, default)
    if required and not v:
        raise SystemExit(f"Missing environment variable {name}")
    return v


def r2():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=env("R2_ENDPOINT"),  # https://<account-id>.r2.cloudflarestorage.com
        aws_access_key_id=env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(retries={"max_attempts": 5, "mode": "standard"}, signature_version="s3v4"),
    )


def head(s3, bucket, key):
    try:
        return s3.head_object(Bucket=bucket, Key=key)
    except s3.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


class Intake:
    def __init__(self):
        self.base = env("INTAKE_API").rstrip("/")
        self.token = env("ADMIN_TOKEN")

    def _h(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def get(self, sid):
        r = requests.get(f"{self.base}/api/admin/submissions/{sid}", headers=self._h(), timeout=30)
        r.raise_for_status()
        return r.json()

    def update(self, sid, **fields):
        try:
            r = requests.post(f"{self.base}/api/admin/submissions/{sid}", headers=self._h(), data=json.dumps(fields), timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:  # status is a courtesy; never fail the job over it
            print(f"warning: could not update status for {sid}: {e}")

    def verify_sig(self, sid, sig):
        good = hmac.new(self.token.encode(), sid.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(good, sig or "")


def gh(*args, input=None, check=True):
    """Run the GitHub CLI (authenticated by GH_TOKEN in Actions)."""
    out = subprocess.run(["gh", *args], input=input, text=True, capture_output=True)
    if check and out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {out.stderr.strip()}")
    return out.stdout


def git(*args, check=True):
    out = subprocess.run(["git", *args], text=True, capture_output=True)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {out.stderr.strip()}")
    return out.stdout


def sha256_path(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()
