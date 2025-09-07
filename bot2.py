import os
import json
import random
import time
from typing import Dict, Any, List

from atproto import Client, models as M, RichText

STATE_FILE = "bot2_state.json"

# --- Secrets GitHub ---
HANDLE = os.getenv("BSKY2_HANDLE")
APP_PASSWORD = os.getenv("BSKY2_APP_PASSWORD")
if not HANDLE or not APP_PASSWORD:
    raise SystemExit("Manque BSKY2_HANDLE ou BSKY2_APP_PASSWORD (Secrets GitHub).")

# --- Réglages sûrs (caps + delays) ---
MAX_ENGAGEMENTS_PER_RUN = int(os.getenv("BOT2_MAX_ENG_PER_RUN", "3"))   # likes/réponses aux mentions
MAX_REPOSTS_PER_RUN     = int(os.getenv("BOT2_REPOST_LIMIT", "2"))      # ← mets 2 pour viser ~2 retweets par run
DELAY_MIN_S             = int(os.getenv("BOT2_DELAY_MIN_S", "12"))
DELAY_MAX_S             = int(os.getenv("BOT2_DELAY_MAX_S", "45"))

# --- Découverte (search) ---
DISCOVERY_WEIGHT   = float(os.getenv("BOT2_DISCOVERY_WEIGHT", "0.7"))   # proba d'activer la phase discovery
DISCOVERY_QUERY    = os.getenv("BOT2_QUERY", "art OR digital art OR nft")
DISCOVERY_LIKE_LIMIT = int(os.getenv("BOT2_LIKE_LIMIT", "3"))

# --- Posts originaux (facultatif) ---
DO_ORIGINAL_POST_WEIGHT = float(os.getenv("BOT2_ORIGINAL_POST_WEIGHT", "0.20"))  # 20% des runs
ORIGINAL_POSTS = [
    "Exploring stories in color and motion 🎨✨",
    "Fiction painted in pixels. More soon.",
    "Sketches from my universe of art & fiction.",
    "Digital brushstrokes, narrative vibes.",
    "Sharing what I love: art, fiction, and a bit of NFTs.",
]
LINK_SITE    = os.getenv("BOT2_LINK_SITE", "https://louphi1987.github.io/Site_de_Louphi/")
LINK_OPENSEA = os.getenv("BOT2_LINK_OPENSEA", "https://opensea.io/fr/collection/loufis-art")
APPEND_LINK_PROB = float(os.getenv("BOT2_APPEND_LINK_PROB", "0.5"))

# --- Sources explicites (opt-in) ---
SOURCE_HANDLES = [h.strip() for h in os.getenv("BOT2_SOURCE_HANDLES", "").split(",") if h.strip()]

# --- Compte et ratio pour retweets cités ---
QUOTE_HANDLE = os.getenv("BOT2_QUOTE_HANDLE", "loufisart.bsky.social")
QUOTE_SHARE  = float(os.getenv("BOT2_QUOTE_SHARE", "0.5"))  # 50%

# --- State helpers ---
def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            import json
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

def search_posts_compat(client: Client, q: str, limit: int = 25):
    try:
        return client.app.bsky.feed.search_posts(q=q, limit=limit)
    except TypeError:
        try:
            return client.app.bsky.feed.search_posts(params={"q": q, "limit": limit})
        except Exception:
            return None

# --- Utils ---
def login() -> Client:
    c = Client()
    c.login(HANDLE, APP_PASSWORD)
    return c

def random_sleep():
    time.sleep(random.uniform(DELAY_MIN_S, DELAY_MAX_S))

def send_post_with_links(client: Client, text: str, embed=None, reply_to=None):
    """
    Envoie un post en détectant automatiquement les liens/handles/hashtags pour créer des 'facets'
    -> garantit que l'URL est vraiment cliquable dans Bluesky.
    """
    rt = RichText(text)
    rt.detect_facets()
    return client.send_post(text=rt.text, facets=rt.facets, embed=embed, reply_to=reply_to)

# --- Core ---
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

def safe_quote_repost(client: Client, uri: str, cid: str, text: str) -> bool:
    try:
        embed = M.AppBskyEmbedRecord.Main(
            record=M.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid)
        )
        send_post_with_links(client, text=text, embed=embed)
        return True
    except Exception as e:
        print(f"[quote err] {e}")
        return False

def safe_reply(client: Client, uri: str, cid: str, text: str) -> bool:
    try:
        reply_ref = M.AppBskyFeedPost.ReplyRef(
            parent=M.AppBskyFeedPost.ReplyRefParent(uri=uri, cid=cid)
        )
        send_post_with_links(client, text=text, reply_to=reply_ref)
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
        resp = send_post_with_links(client, text=text)
        print(f"Original post: {text} -> {getattr(resp, 'uri', '')}")
    except Exception as e:
        print(f"[post err] {e}")

# --- Templates quote-retweets ---
QUOTE_TEMPLATES = [
    "New artist to discover ✨\nFresh vibes and bold colors.\nExplore the collection: {link}",
    "A unique voice in digital art 🎨\nStorytelling through pixels.\nDiscover more: {link}",
    "If you love narrative visuals, this is for you 💫\nWorth a closer look.\nDive in: {link}",
    "Clean lines, vivid worlds.\nOne more artist to watch 👀\nSee the works: {link}",
    "New drops + timeless style.\nCurated for curious eyes 🧭\nStart here: {link}",
]

def build_quote_text() -> str:
    return random.choice(QUOTE_TEMPLATES).format(link=LINK_OPENSEA)

def pick_latest_original_post_from_actor(client: Client, actor: str, limit: int = 10):
    try:
        feed = get_author_feed_compat(client, actor=actor, limit=limit)
        for item in (getattr(feed, "feed", []) or []):
            post = getattr(item, "post", None)
            if not post:
                continue
            if (getattr(post, "reply", None) is not None) or (getattr(post, "repost", None) is not None):
                continue
            return post
    except Exception as e:
        print(f"[pick original err:{actor}] {e}")
    return None

def repost_from_sources_with_quotes(client: Client):
    if MAX_REPOSTS_PER_RUN <= 0:
        print("Repost cap is 0. Skipping reposts.")
        return

    target_quote_count = max(0, int(round(MAX_REPOSTS_PER_RUN * QUOTE_SHARE)))
    done_reposts = 0
    done_quotes = 0

    # 1) Quote-retweets de QUOTE_HANDLE
    while done_quotes < target_quote_count and done_reposts < MAX_REPOSTS_PER_RUN:
        p = pick_latest_original_post_from_actor(client, QUOTE_HANDLE, limit=8)
        if not p:
            print("No original post found to quote from QUOTE_HANDLE.")
            break
        if safe_quote_repost(client, p.uri, p.cid, build_quote_text()):
            done_quotes += 1
            done_reposts += 1
            print(f"Quote-retweet from @{QUOTE_HANDLE}: {p.uri}")
            random_sleep()
        else:
            break

    # 2) Reposts simples depuis SOURCE_HANDLES (hors QUOTE_HANDLE)
    sources = [h for h in SOURCE_HANDLES if h and h != QUOTE_HANDLE]
    random.shuffle(sources)
    for actor in sources:
        if done_reposts >= MAX_REPOSTS_PER_RUN:
            break
        try:
            feed = get_author_feed_compat(client, actor=actor, limit=5)
            for item in (getattr(feed, "feed", []) or []):
                post = getattr(item, "post", None)
                if not post or (post.reply is not None) or (post.repost is not None):
                    continue
                if safe_repost(client, post.uri, post.cid):
                    done_reposts += 1
                    print(f"Repost (simple) from {actor}: {post.uri}")
                    random_sleep()
                    break
        except Exception as e:
            print(f"[source err:{actor}] {e}")
            continue

    # 3) Compléter via discovery si besoin
    if done_reposts < MAX_REPOSTS_PER_RUN:
        remaining = MAX_REPOSTS_PER_RUN - done_reposts
        extra = repost_via_discovery(client, remaining)
        done_reposts += extra

    print(f"Reposts done: {done_reposts} (quotes={done_quotes}, cap={MAX_REPOSTS_PER_RUN})")

def repost_via_discovery(client: Client, remaining_needed: int) -> int:
    if remaining_needed <= 0:
        return 0
    try:
        res = search_posts_compat(client, q=DISCOVERY_QUERY, limit=30)
        posts = getattr(res, "posts", []) or []
        random.shuffle(posts)
        count = 0
        for p in posts:
            if count >= remaining_needed:
                break
            try:
                if getattr(p, "reply", None) is not None or getattr(p, "repost", None) is not None:
                    continue
            except Exception:
                pass
            if safe_repost(client, p.uri, p.cid):
                count += 1
                print(f"Repost via discovery: {p.uri}")
                random_sleep()
        return count
    except Exception as e:
        print(f"[discovery repost err] {e}")
        return 0

def discovery_likes(client: Client):
    if random.random() >= DISCOVERY_WEIGHT:
        print("Skip discovery this run (weight check).")
        return
    try:
        res = search_posts_compat(client, q=DISCOVERY_QUERY, limit=40)
        posts = getattr(res, "posts", []) or []
        random.shuffle(posts)
        likes_done = 0
        for p in posts:
            if likes_done >= DISCOVERY_LIKE_LIMIT:
                break
            uri = getattr(p, "uri", None)
            cid = getattr(p, "cid", None)
            if not uri or not cid:
                continue
            if safe_like(client, uri, cid):
                likes_done += 1
                print(f"Discovery like: {uri}")
                random_sleep()
        print(f"Discovery likes done: {likes_done}/{DISCOVERY_LIKE_LIMIT}")
    except Exception as e:
        print(f"[discovery err] {e}")

# --- Main ---
if __name__ == "__main__":
    client = login()

    # 1) Engagements opt-in (mentions/réponses)
    state = load_state()
    engage_opt_in(client, state)

    # 2) Occasionnellement, un post original
    maybe_original_post(client)

    # 3) Découverte (likes)
    discovery_likes(client)

    # 4) Reposts (dont 50% de quote-retweets depuis @loufisart)
    repost_from_sources_with_quotes(client)

    print("Bot2 run completed (anti-spam mode).")
