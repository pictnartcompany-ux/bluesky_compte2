import os
import json
import random
import time
import datetime as dt
from typing import Optional, Dict, Any, List

from atproto import Client, models as M

STATE_FILE = "bot2_state.json"

# --- Secrets GitHub ---
HANDLE = os.getenv("BSKY2_HANDLE")
APP_PASSWORD = os.getenv("BSKY2_APP_PASSWORD")
if not HANDLE or not APP_PASSWORD:
    raise SystemExit("Manque BSKY2_HANDLE ou BSKY2_APP_PASSWORD (Secrets GitHub).")

# --- Réglages sûrs (caps + delays) ---
MAX_ENGAGEMENTS_PER_RUN = int(os.getenv("BOT2_MAX_ENG_PER_RUN", "3"))   # likes/replies par exécution
MAX_REPOSTS_PER_RUN = int(os.getenv("BOT2_MAX_REPOSTS_PER_RUN", "1"))
DELAY_MIN_S = int(os.getenv("BOT2_DELAY_MIN_S", "12"))
DELAY_MAX_S = int(os.getenv("BOT2_DELAY_MAX_S", "45"))

# Posts originaux (facultatif)
DO_ORIGINAL_POST_WEIGHT = float(os.getenv("BOT2_ORIGINAL_POST_WEIGHT", "0.20"))  # 20% des runs
ORIGINAL_POSTS = [
    "Exploring stories in color and motion 🎨✨",
    "Fiction painted in pixels. More soon.",
    "Sketches from my universe of art & fiction.",
    "Digital brushstrokes, narrative vibes.",
    "Sharing what I love: art, fiction, and a bit of NFTs.",
]
LINK_SITE = os.getenv("BOT2_LINK_SITE", "https://louphi1987.github.io/Site_de_Louphi/")
LINK_OPENSEA = os.getenv("BOT2_LINK_OPENSEA", "https://opensea.io/collection/loufis-art")
APPEND_LINK_PROB = float(os.getenv("BOT2_APPEND_LINK_PROB", "0.5"))

# Sources explicites (comptes qui ont donné opt‑in) — une repost max
SOURCE_HANDLES = [h.strip() for h in os.getenv("BOT2_SOURCE_HANDLES", "").split(",") if h.strip()]

# --- State helpers ---

def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"processed_notifications": []}


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# --- SDK compat ---

def list_notifications_compat(client: Client, limit: int = 40):
    try:
        return client.app.bsky.notification.list_notifications(limit=limit)
    except TypeError:
        return client.app.bsky.notification.list_notifications()


def get_author_feed_compat(client: Client, actor: str, limit: int = 5):
    try:
        return client.app.bsky.feed.get_author_feed(actor=actor, limit=limit)
    except TypeError:
        return client.app.bsky.feed.get_author_feed(params={"actor": actor, "limit": limit})


# --- Core logic (opt‑in only) ---

def login() -> Client:
    c = Client()
    c.login(HANDLE, APP_PASSWORD)
    return c


def random_sleep():
    time.sleep(random.uniform(DELAY_MIN_S, DELAY_MAX_S))


def fetch_mentions_and_replies(client: Client, state: Dict[str, Any]) -> List[Any]:
    res = list_notifications_compat(client, limit=50)
    items = getattr(res, "notifications", []) or []
    processed = set(state.get("processed_notifications", []))
    fresh = []
    for n in items:
        reason = getattr(n, "reason", None)
        if reason not in ("mention", "reply"):
            continue
        nid = getattr(n, "cid", None) or getattr(n, "id", None) or getattr(n, "uri", None)
        if not nid or nid in processed:
            continue
        fresh.append(n)
    return fresh


def safe_like(client: Client, uri: str, cid: str) -> bool:
    try:
        client.like(uri=uri, cid=cid)
        return True
    except Exception as e:
        print(f"[like err] {e}")
        return False


def safe_repost(client: Client, uri: str, cid: str) -> bool:
    try:
        client.repost(uri=uri, cid=cid)
        return True
    except Exception as e:
        print(f"[repost err] {e}")
        return False


def safe_reply(client: Client, uri: str, cid: str, text: str) -> bool:
    try:
        client.send_post(
            text=text,
            reply_to=M.AppBskyFeedPost.ReplyRef(
                parent=M.AppBskyFeedPost.ReplyRefParent(uri=uri, cid=cid)
            ),
        )
        return True
    except Exception as e:
        print(f"[reply err] {e}")
        return False


def engage_opt_in(client: Client, state: Dict[str, Any]):
    mentions = fetch_mentions_and_replies(client, state)
    random.shuffle(mentions)
    engagements = 0
    for n in mentions:
        if engagements >= MAX_ENGAGEMENTS_PER_RUN:
            break
        uri = getattr(n, "uri", None)
        cid = getattr(n, "cid", None)
        if not uri or not cid:
            continue
        # 75% like, 25% petite réponse
        if random.random() < 0.75:
            if safe_like(client, uri, cid):
                engagements += 1
                print(f"Like mention: {uri}")
        else:
            reply_text = random.choice(["Thanks!", "Appreciate it 🙏", "Thanks for the tag ✨"]) \
                         if random.random() < 0.7 else random.choice(["✨", "👏", "👍"]) 
            if safe_reply(client, uri, cid, reply_text):
                engagements += 1
                print(f"Reply mention: {uri} -> {reply_text}")
        # mark processed
        nid = getattr(n, "cid", None) or getattr(n, "id", None) or getattr(n, "uri", None)
        if nid:
            state.setdefault("processed_notifications", []).append(nid)
            state["processed_notifications"] = state["processed_notifications"][-500:]
            save_state(state)
        random_sleep()


def maybe_original_post(client: Client):
    if random.random() >= DO_ORIGINAL_POST_WEIGHT:
        print("Skip original post this run.")
        return
    text = random.choice(ORIGINAL_POSTS)
    if random.random() < APPEND_LINK_PROB:
        text += " " + (LINK_SITE if random.random() < 0.5 else LINK_OPENSEA)
    try:
        resp = client.send_post(text=text)
        print(f"Original post: {text} -> {getattr(resp, 'uri', '')}")
    except Exception as e:
        print(f"[post err] {e}")


def repost_from_sources(client: Client):
    # Reposter uniquement si sources explicites (opt‑in), 1 repost max
    if not SOURCE_HANDLES or MAX_REPOSTS_PER_RUN <= 0:
        print("No SOURCE_HANDLES or repost cap is 0. Skipping source reposts.")
        return
    reposted = 0
    for actor in SOURCE_HANDLES:
        if reposted >= MAX_REPOSTS_PER_RUN:
            break
        try:
            feed = get_author_feed_compat(client, actor=actor, limit=5)
            for item in (getattr(feed, "feed", []) or []):
                post = getattr(item, "post", None)
                # Reposter uniquement des posts originaux (pas replies/reposts)
                if not post or (post.reply is not None) or (post.repost is not None):
                    continue
                if safe_repost(client, post.uri, post.cid):
                    reposted += 1
                    print(f"Repost from {actor}: {post.uri}")
                    random_sleep()
                    break
        except Exception as e:
            print(f"[source err:{actor}] {e}")
            continue


if __name__ == "__main__":
    client = login()

    # 1) Engagements opt‑in (mentions/réponses) – prioritaire
    state = load_state()
    engage_opt_in(client, state)

    # 2) Occasionnellement, un post original
    maybe_original_post(client)

    # 3) Repost limité de sources explicites (opt‑in)
    repost_from_sources(client)

    print("Bot2 run completed (anti‑spam mode).")
