"""Upload one already-approved public production using YouTube Data API v3."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.youtube.publisher import YouTubePublishError, YouTubePublisher
from app.youtube.repository import get_approval, get_upload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("production_id", help="Production ID with a completed, approved public upload record")
    args = parser.parse_args()
    if os.getenv("RUN_LIVE_YOUTUBE") != "1":
        print("Refusing live upload: set RUN_LIVE_YOUTUBE=1 after reviewing the production and OAuth project.", file=sys.stderr)
        return 2

    publisher = YouTubePublisher()
    approval = get_approval(args.production_id)
    if not approval or approval["state"] not in {"approved", "uploading", "failed"}:
        print("The production does not have an approved YouTube publishing record.", file=sys.stderr)
        return 2
    if approval["visibility"] != "public":
        print("The approved publishing visibility is not public; refusing the live smoke upload.", file=sys.stderr)
        return 2
    existing = get_upload(args.production_id)
    try:
        result = publisher.upload(args.production_id)
    except YouTubePublishError as error:
        print(f"Live YouTube upload failed: {error}", file=sys.stderr)
        return 1
    video_id = result.get("video_id") or (existing or {}).get("video_id")
    if not video_id:
        print("YouTube did not return a recorded video ID.", file=sys.stderr)
        return 1
    url = result.get("video_url") or f"https://www.youtube.com/watch?v={video_id}"
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())