#!/usr/bin/env python3
"""Upload a Cygnet release to Zenodo.

Usage: python3 scripts/zenodo_upload.py TAG

Requires ZENODO_TOKEN env var and GH_TOKEN for downloading release assets.

Reads metadata from .zenodo.json. If .zenodo.json contains a 'conceptdoi'
field, creates a new version of that record. Otherwise creates a fresh deposit.

After the first publish, add the printed concept DOI to .zenodo.json:
    "conceptdoi": "10.5281/zenodo.XXXXXXX"
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://zenodo.org/api"


def api(method, path, token, data=None):
    url = path if path.startswith("http") else f"{BASE}{path}"
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()) if r.length != 0 else {}
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {method} {url}: {e.read().decode()}", file=sys.stderr)
        raise


def upload_file(bucket_url, filepath, token):
    fname = Path(filepath).name
    print(f"  Uploading {fname} ({Path(filepath).stat().st_size // 1024} KB)...")
    with open(filepath, "rb") as f:
        req = urllib.request.Request(
            f"{bucket_url}/{fname}",
            data=f.read(),
            headers={"Authorization": f"Bearer {token}"},
            method="PUT",
        )
        with urllib.request.urlopen(req) as r:
            return r.status


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} TAG", file=sys.stderr)
        sys.exit(1)

    tag = sys.argv[1]
    token = os.environ.get("ZENODO_TOKEN")
    if not token:
        print("ZENODO_TOKEN not set — skipping Zenodo upload")
        sys.exit(0)

    with open(".zenodo.json") as f:
        meta = json.load(f)
    meta["version"] = tag
    concept_doi = meta.pop("conceptdoi", None)

    # Create or update deposit
    if concept_doi:
        concept_id = concept_doi.split("zenodo.")[-1]
        print(f"Creating new version from concept DOI {concept_doi}...")
        draft = api("POST", f"/deposit/depositions/{concept_id}/actions/newversion", token)
        latest_url = draft["links"]["latest_draft"]
        deposit_id = latest_url.split("/")[-1]
        deposit = api("GET", f"/deposit/depositions/{deposit_id}", token)
        bucket_url = deposit["links"]["bucket"]
        for f in api("GET", f"/deposit/depositions/{deposit_id}/files", token):
            api("DELETE", f"/deposit/depositions/{deposit_id}/files/{f['id']}", token)
    else:
        print("Creating new deposit...")
        deposit = api("POST", "/deposit/depositions", token, {})
        deposit_id = deposit["id"]
        bucket_url = deposit["links"]["bucket"]

    # Set metadata
    api("PUT", f"/deposit/depositions/{deposit_id}", token, {"metadata": meta})
    print(f"Metadata set (deposit {deposit_id})")

    # Download release assets and upload
    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"Downloading release assets for {tag}...")
        subprocess.run(
            ["gh", "release", "download", tag, "--repo", "omwn/cygnet", "--dir", tmpdir],
            check=True,
        )
        for fname in ["cygnet.db.gz", "provenance.db.gz"]:
            fpath = Path(tmpdir) / fname
            if fpath.exists():
                status = upload_file(bucket_url, fpath, token)
                print(f"  → HTTP {status}")
            else:
                print(f"WARNING: {fname} not found in release assets", file=sys.stderr)

    # Publish
    print("Publishing...")
    result = api("POST", f"/deposit/depositions/{deposit_id}/actions/publish", token)
    print(f"\nDone!")
    print(f"  DOI:         {result['doi']}")
    print(f"  Concept DOI: {result['conceptdoi']}")
    print(f"\nAdd to .zenodo.json for future versions:")
    print(f'  "conceptdoi": "{result["conceptdoi"]}"')


if __name__ == "__main__":
    main()
