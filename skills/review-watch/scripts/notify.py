"""Print a message and, when Slack is set up, DM it to you from a bot.

A bot's DM pushes to your phone; a message you post to yourself doesn't. Set
REVIEW_WATCH_SLACK_BOT_TOKEN (a bot token with chat:write) and REVIEW_WATCH_SLACK_USER_ID (your
Slack member ID) to get the DM; without them the message is only printed.

Usage: notify.py '<message>'
"""

import json
import os
import sys
import urllib.parse
import urllib.request

BOT_TOKEN = "REVIEW_WATCH_SLACK_BOT_TOKEN"
USER_ID = "REVIEW_WATCH_SLACK_USER_ID"


def send_dm(token: str, user_id: str, text: str) -> None:
    """Post text to the user's DM with the bot; exit on a Slack error."""
    request = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=urllib.parse.urlencode({"channel": user_id, "text": text, "unfurl_links": "false"}).encode(),
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.load(response)
    if not body.get("ok"):
        raise SystemExit(f"Slack chat.postMessage failed: {body.get('error')}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: notify.py '<message>'")
    text = sys.argv[1]
    print(text, flush=True)
    token, user_id = os.environ.get(BOT_TOKEN), os.environ.get(USER_ID)
    if token and user_id:
        send_dm(token, user_id, text)


if __name__ == "__main__":
    main()
