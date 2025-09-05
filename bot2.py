# bot2.py
import os
import random
import time
from atproto import Client

# Identifiants (Secrets GitHub)
HANDLE = os.getenv("BSKY2_HANDLE")
APP_PASSWORD = os.getenv("BSKY2_APP_PASSWORD")

if not HANDLE or not APP_PASSWORD:
    raise SystemExit("Manque BSKY2_HANDLE ou BSKY2_APP_PASSWORD (Secrets GitHub).")

# Réglages (Variables GitHub côté repo)
QUERY = os.getenv("BOT2_QUERY", "art OR digital art OR nft")
DISCOVERY_WEIGHT = float(os.getenv("BOT2_DISCOVERY_WEIGHT", "0.7"))  # 70% découvertes / 30% repost sources
LIKE_LIMIT = int(os.getenv("BOT2_LIKE_LIMIT", "3"))
REPOST_LIMIT = int(os.getenv("BOT2_REPOST_LIMIT", "1"))
SOURCE_HANDLES = [h.strip() for h in os.getenv("BOT2_SOURCE_HANDLES", "").split(",") if h.strip()]

def login():
    client = Client()
    client.login(HANDLE, APP_PASSWORD)
    return client

def discover_and_engage(client: Client):
    liked = 0
    reposted = 0
    res = client.app.bsky.feed.search_posts(params={"q": QUERY, "limit": 30, "sort": "latest"})
    for post in (res.posts or []):
        uri, cid = post.uri, post.cid
        # Like
        if liked < LIKE_LIMIT:
            try:
                client.like(uri=uri, cid=cid)
                liked += 1
                time.sleep(1.2)
            except Exception:
                pass
        # Petit repost (max 1) pour rester soft
        if reposted < min(1, REPOST_LIMIT):
            try:
                client.repost(uri=uri, cid=cid)
                reposted += 1
                time.sleep(1.2)
            except Exception:
                pass
        if liked >= LIKE_LIMIT and reposted >= min(1, REPOST_LIMIT):
            break

def repost_from_sources(client: Client):
    if not SOURCE_HANDLES:
        return
    reposted = 0
    for actor in SOURCE_HANDLES:
        try:
            feed = client.get_author_feed(actor, limit=5)
            if not feed.feed:
                continue
            for item in feed.feed:
                post = item.post
                # Reposter uniquement des posts “originaux” (pas replies/reposts)
                if not post or (post.reply is not None) or (post.repost is not None):
                    continue
                client.repost(uri=post.uri, cid=post.cid)
                reposted += 1
                time.sleep(1.2)
                break  # un repost par source max
            if reposted >= REPOST_LIMIT:
                break
        except Exception:
            continue

if __name__ == "__main__":
    client = login()
    # Tirage pondéré : 70% découvertes, 30% repost des sources (ajuste DISCOVERY_WEIGHT)
    if random.random() < DISCOVERY_WEIGHT:
        discover_and_engage(client)
    else:
        repost_from_sources(client)
    print("Bot2 run completed.")
