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
MAX_ENGAGEMENTS_PER_RUN = int(os.getenv("BOT2_MAX_ENG_PER_RUN", "3"))   # likes/réponses aux mentions
MAX_REPOSTS_PER_RUN = int(os.getenv("BOT2_REPOST_LIMIT", "2"))          # <-- 2 pour ~deux retweets possibles par run
DELAY_MIN_S = int(os.getenv("BOT2_DELAY_MIN_S", "12"))
DELAY_MAX_S = int(os.getenv("BOT2_DELAY_MAX_S", "45"))

# --- Découverte (search) ---
DISCOVERY_WEIGHT = float(os.getenv("BOT2_DISCOVERY_WEIGHT", "0.7"))     # probabilité d'activer la phase discovery
DISCOVERY_QUERY = os.getenv("BOT2_QUERY", "art OR digital art OR nft")
DISCOVERY_LIKE_LIMIT = int(os.getenv("BOT2_LIKE_LIMIT", "3"))           # nb max de likes via discovery

# --- Posts originaux (facultatif) ---
DO_ORIGINAL_POST_WEIGHT = float(os.getenv("BOT2_ORIGINAL_POST_WEIGHT", "0.20"))  # 20% des runs
ORIGINAL_POSTS = [
    "Exploring stories in color and motion 🎨✨",
    "Fiction painted in pixels. More soon.",
    "Sketches from my universe of art & fiction.",
    "Digital brushstrokes, narrative vibes.",
    "Sharing what I love: art, fiction, and a bit of NFTs.",
]
LINK_SITE = os.getenv("BOT2_LINK_SITE", "https://louphi1987.github.io/Site_de_Louphi/")
# On respecte exactement le lien que tu as fourni (version FR sur OpenSea)
LINK_OPENSEA = os.getenv("BOT2_LINK_OPENSEA", "https://opensea.io/fr/collection/loufis-art")
APPEND_LINK_PROB = float(os.getenv("BOT2_APPEND_LINK_PROB", "0.5"))

# --- Sources explicites (opt-in) : une repost max/compte par run (hors discovery)
SOURCE_HANDLES = [h.strip() for h in os.getenv("BOT2_SOURCE_HANDLES", "").split(",") if h.strip()]

# --- Compte à citer pour 50% des retweets ---
QUOTE_HANDLE = os.getenv("BOT2_QUOTE_HANDLE", "loufisart.bsky.social")
QUOTE_SHARE = float(os.getenv("BOT2_QUOTE_SHARE", "0.5"))  # 50%

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

def search_posts_compat(client: Client, q: str, limit: int = 25):
    """
    Recherche de posts pour la découverte (selon la version du SDK).
    """
    try:
        return client.app.bsky.feed.search_posts(q=q, limit=limit)
    except TypeError:
        # Anciennes signatures
        try:
            return client.app.bsky.feed.search_posts(params={"q": q, "limit": limit})
        except Exception:
            return None

# --- Core ---
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

def safe_quote_repost(client: Client, uri: str, cid: str, text: str) -> bool:
    """
    Retweet cité = nouveau post avec embed record pointant vers le post source.
    """
    try:
        embed = M.AppBskyEmbedRecord.Main(
            record=M.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid)
        )
        client.send_post(text=text, embed=embed)
        return True
    except Exception as e:
        print(f"[quote err] {e}")
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

# --- Textes pour retweets cités (anglais, multi-phrases courtes + emoji + lien cliquable) ---
QUOTE_TEMPLATES = [
    "New artist to discover ✨\nFresh vibes and bold colors.\nExplore the collection: {link}",
    "A unique voice in digital art 🎨\nStorytelling through pixels.\nDiscover more: {link}",
    "If you love narrative visuals, this is for you 💫\nWorth a closer look.\nDive in: {link}",
    "Clean lines, vivid worlds.\nOne more artist to watch 👀\nSee the works: {link}",
    "New drops + timeless style.\nCurated for curious eyes 🧭\nStart here: {link}",
]

def build_quote_text() -> str:
    t = random.choice(QUOTE_TEMPLATES)
    return t.format(link=LINK_OPENSEA)

def pick_latest_original_post_from_actor(client: Client, actor: str, limit: int = 10):
    """
    Récupère le dernier post original (ni reply, ni repost) d'un acteur.
    """
    try:
        feed = get_author_feed_compat(client, actor=actor, limit=limit)
        for item in (getattr(feed, "feed", []) or []):
            post = getattr(item, "post", None)
            if not post:
                continue
            # Exclure replies/reposts
            if (getattr(post, "reply", None) is not None) or (getattr(post, "repost", None) is not None):
                continue
            return post
    except Exception as e:
        print(f"[pick original err:{actor}] {e}")
    return None

def repost_from_sources_with_quotes(client: Client):
    """
    Gère TOUTES les reposts du run, en appliquant la contrainte :
    - ~50% des retweets = retweets cités du compte QUOTE_HANDLE, avec texte anglais + emoji + lien OpenSea
    - le reste = reposts simples issus des SOURCE_HANDLES (opt-in) ou, à défaut, d'une découverte
    """
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
        ok = safe_quote_repost(client, p.uri, p.cid, build_quote_text())
        if ok:
            done_quotes += 1
            done_reposts += 1
            print(f"Quote-retweet from @{QUOTE_HANDLE}: {p.uri}")
            random_sleep()
        else:
            break

    # 2) Reposts simples depuis SOURCE_HANDLES (hors QUOTE_HANDLE pour éviter doublons)
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

    # 3) Si on n'a pas atteint le quota de reposts, on tente via discovery (repost simple)
    if done_reposts < MAX_REPOSTS_PER_RUN:
        remaining = MAX_REPOSTS_PER_RUN - done_reposts
        extra = repost_via_discovery(client, remaining)
        done_reposts += extra

    print(f"Reposts done: {done_reposts} (quotes={done_quotes}, cap={MAX_REPOSTS_PER_RUN})")

def repost_via_discovery(client: Client, remaining_needed: int) -> int:
    """
    Reposts simples issus d'une recherche si besoin pour compléter le quota.
    """
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
            # Exclure reply/repost si champs présents (search_posts renvoie des AppBskyFeedDefs.PostView)
            # On vérifie prudemment selon structure du SDK.
            # Si ces attributs n'existent pas, on tente quand même.
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

def discovery_likes_and_maybe_reposts(client: Client):
    """
    Phase découverte (activée avec probabilité DISCOVERY_WEIGHT) :
    - Likes jusqu'à DISCOVERY_LIKE_LIMIT
    - Reposts sont gérés par repost_from_sources_with_quotes() pour respecter le 50% cités.
    """
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

if __name__ == "__main__":
    client = login()

    # 1) Engagements opt-in (mentions/réponses) – prioritaire
    state = load_state()
    engage_opt_in(client, state)

    # 2) Occasionnellement, un post original
    maybe_original_post(client)

    # 3) Découverte (likes) selon poids
    discovery_likes_and_maybe_reposts(client)

    # 4) Reposts (incluant 50% de quote-retweets sur @loufisart)
    repost_from_sources_with_quotes(client)

    print("Bot2 run completed (anti-spam mode).")
