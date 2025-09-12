import os
import json
import random
import time
import datetime as dt
from typing import Dict, Any, List, Optional, Tuple
from atproto import Client, models as M

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    from backports.zoneinfo import ZoneInfo  # type: ignore

STATE_FILE = "bot2_state.json"

# --- Time window (Europe/Brussels) ---
TIMEZONE = "Europe/Brussels"
EVENING_START = 19  # 19:00 inclus
EVENING_END = 23    # 23:00 exclu
QUIET_START = 23    # nuit 23:00
QUIET_END = 7       # -> 07:00

def _now_local():
    return dt.datetime.now(ZoneInfo(TIMEZONE))

def _is_evening(now_local: dt.datetime) -> bool:
    return EVENING_START <= now_local.hour < EVENING_END

def _is_quiet(now_local: dt.datetime) -> bool:
    h = now_local.hour
    if QUIET_START <= QUIET_END:
        return QUIET_START <= h < QUIET_END
    # cas wrap
    return h >= QUIET_START or h < QUIET_END

# --- Secrets GitHub ---
HANDLE = os.getenv("BSKY2_HANDLE")
APP_PASSWORD = os.getenv("BSKY2_APP_PASSWORD")
if not HANDLE or not APP_PASSWORD:
    raise SystemExit("Manque BSKY2_HANDLE ou BSKY2_APP_PASSWORD (Secrets GitHub).")

# --- Réglages sûrs (caps + delays) ---
MAX_ENGAGEMENTS_PER_RUN = int(os.getenv("BOT2_MAX_ENG_PER_RUN", "3"))  # likes/réponses aux mentions
MAX_REPOSTS_PER_RUN = int(os.getenv("BOT2_REPOST_LIMIT", "2"))         # ~2 reposts par run
DELAY_MIN_S = int(os.getenv("BOT2_DELAY_MIN_S", "12"))
DELAY_MAX_S = int(os.getenv("BOT2_DELAY_MAX_S", "45"))

# --- Anti-doublon d'URI (durée) ---
POST_COOLDOWN_DAYS = int(os.getenv("BOT2_POST_COOLDOWN_DAYS", "14"))

# --- Diversité / Cooldown par source/domaine ---
COOLDOWN_DAYS = int(os.getenv("BOT2_SOURCE_COOLDOWN_DAYS", "3"))

# --- Découverte (search) ---
LEGACY_QUERY = os.getenv("BOT2_QUERY", "").strip()  # option A
QUERIES_ENV = os.getenv("BOT2_QUERIES", "")  # option B "q1|q2|q3"
QUERIES_ENV = [q.strip() for q in QUERIES_ENV.split("|") if q.strip()]
DEFAULT_QUERIES = [
    "nft art OR #nftart OR cryptoart OR #cryptoart",
    "#nft drop OR 1/1 art OR #oneofone",
    "digital art #nft OR generative art OR #genart",
    "zora OR foundation OR superrare OR manifold OR opensea",
    "tezos art OR #tezos OR fxhash",
    "art numérique #nft OR cryptoart #art",
]

def _build_queries() -> List[str]:
    if QUERIES_ENV:
        return QUERIES_ENV
    if LEGACY_QUERY:
        return [LEGACY_QUERY]
    return DEFAULT_QUERIES[:]

DISCOVERY_LIKE_LIMIT = int(os.getenv("BOT2_LIKE_LIMIT", "3"))
DISCOVERY_WEIGHT = float(os.getenv("BOT2_DISCOVERY_WEIGHT", "0.7"))

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
LINK_OPENSEA = os.getenv("BOT2_LINK_OPENSEA", "https://opensea.io/fr/collection/loufis-art")
APPEND_LINK_PROB = float(os.getenv("BOT2_APPEND_LINK_PROB", "0.5"))
# ⚠️ Nouveau comportement: quand un lien est choisi, on le poste en COMMENTAIRE (reply),
# pas dans le post principal (plus cliquable sur Bluesky).

# --- Sources explicites (opt-in) ---
SOURCE_HANDLES = [h.strip() for h in os.getenv("BOT2_SOURCE_HANDLES", "").split(",") if h.strip()]

# --- Compte 1 (seul autorisé à recevoir un quote avec texte + lien en COMMENTAIRE) ---
QUOTE_HANDLE = os.getenv("BOT2_QUOTE_HANDLE", "loufisart.bsky.social")
QUOTE_SHARE = float(os.getenv("BOT2_QUOTE_SHARE", "0.5"))  # part cible des quotes dans les reposts

# --- Heuristiques "art vs article" ---
MARKET_DOMAINS = set(
    d.strip()
    for d in os.getenv(
        "BOT2_MARKET_DOMAINS",
        "opensea.io,foundation.app,superrare.com,zora.co,manifold.xyz,objkt.com,fxhash.xyz,teia.art,versum.xyz,rarible.com",
    ).split(",")
    if d.strip()
)
ARTICLE_DOMAINS = set(
    d.strip()
    for d in os.getenv("BOT2_ARTICLE_DOMAINS", "medium.com,substack.com,mirror.xyz,blog,news").split(",")
    if d.strip()
)
KEYWORDS_GOOD = {
    "nft","nftart","cryptoart","genart","generative","mint","drop","1/1","oneofone",
    "tezos","fxhash","manifold","zora","opensea","foundation","superrare",
}
KEYWORDS_BAD = {"article","news","thread","analysis","opinion","market report"}

# --- State helpers ---
def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            s.setdefault("processed_notifications", [])
            s.setdefault("recent_sources", [])  # [{actor, ts}]
            s.setdefault("recent_domains", [])  # [{domain, ts}]
            s.setdefault("recent_posts", [])    # [{uri, ts}]
            return s
        except Exception:
            pass
    return {
        "processed_notifications": [],
        "recent_sources": [],
        "recent_domains": [],
        "recent_posts": [],
    }


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


def safe_quote_repost(client: Client, uri: str, cid: str, text: str, link: Optional[str] = None) -> bool:
    """Quote (embed) a post with optional text. If link is provided, put it in a FOLLOW-UP REPLY
    so it's clearly clickable on Bluesky.
    """
    try:
        embed = M.AppBskyEmbedRecord.Main(
            record=M.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid)
        )
        resp = client.send_post(text=text, embed=embed)
        # link en commentaire (reply) pour cliquabilité
        if link:
            try:
                client.send_post(
                    text=link,
                    reply_to=M.AppBskyFeedPost.ReplyRef(
                        parent=M.AppBskyFeedPost.ReplyRefParent(uri=getattr(resp, "uri", uri), cid=getattr(resp, "cid", cid))
                    ),
                )
            except Exception as e:
                print(f"[quote link-reply err] {e}")
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

# --- Anti-doublons URI ---

def _uri_recent(state: Dict[str, Any], uri: str) -> bool:
    if not uri:
        return True
    try:
        cutoff = dt.date.today() - dt.timedelta(days=POST_COOLDOWN_DAYS)
        for it in reversed(state.get("recent_posts", [])):
            if it.get("uri") == uri:
                when = dt.date.fromisoformat(it.get("ts", "1970-01-01"))
                if when >= cutoff:
                    return True
    except Exception:
        pass
    return False


def _remember_uri(state: Dict[str, Any], uri: str) -> None:
    if not uri:
        return
    state.setdefault("recent_posts", []).append({"uri": uri, "ts": dt.date.today().isoformat()})
    state["recent_posts"] = state["recent_posts"][-500:]
    save_state(state)

# --- Diversité / cooldown source & domaine ---

def _is_cooled(entries: List[Dict[str, str]], key: str, value: str) -> bool:
    if not value:
        return True
    try:
        cutoff = dt.date.today() - dt.timedelta(days=COOLDOWN_DAYS)
        for it in reversed(entries):
            if it.get(key) == value:
                when = dt.date.fromisoformat(it.get("ts", "1970-01-01"))
                if when >= cutoff:
                    return False
    except Exception:
        pass
    return True


def _record_source_and_domain(state: Dict[str, Any], actor: str, domains: List[str]) -> None:
    if actor:
        state.setdefault("recent_sources", []).append({"actor": actor, "ts": dt.date.today().isoformat()})
        state["recent_sources"] = state["recent_sources"][-200:]
    if domains:
        d = domains[0]
        state.setdefault("recent_domains", []).append({"domain": d, "ts": dt.date.today().isoformat()})
        state["recent_domains"] = state["recent_domains"][-200:]
    save_state(state)

# --- Quote templates (texte sans lien) ---
QUOTE_TEMPLATES = [
    "New artist to discover ✨\nFresh vibes and bold colors.",
    "A unique voice in digital art 🎨\nStorytelling through pixels.",
    "If you love narrative visuals, this is for you 💫\nWorth a closer look.",
    "Clean lines, vivid worlds.\nOne more artist to watch 👀",
    "New drops + timeless style.\nCurated for curious eyes 🧭",
]


def build_quote_text_and_link() -> Tuple[str, str]:
    text = random.choice(QUOTE_TEMPLATES)
    # Par défaut on pousse le lien OpenSea (modifiable via env)
    return text, LINK_OPENSEA

# --- Helpers "art vs article" + util ---

def _get_embed(p):
    try:
        return getattr(p, "embed", None)
    except Exception:
        return None


def _extract_domains_from_post(p) -> List[str]:
    domains = []
    e = _get_embed(p)
    try:
        if e and getattr(e, "$type", "").endswith("embed.external#view"):
            uri = getattr(getattr(e, "external", None), "uri", "") or ""
            if "://" in uri:
                domains.append(uri.split("://", 1)[1].split("/", 1)[0].lower())
        if e and getattr(e, "$type", "").endswith("embed.recordWithMedia#view"):
            media = getattr(e, "media", None)
            if media and getattr(media, "$type", "").endswith("embed.external#view"):
                uri = getattr(getattr(media, "external", None), "uri", "") or ""
                if "://" in uri:
                    domains.append(uri.split("://", 1)[1].split("/", 1)[0].lower())
    except Exception:
        pass
    return domains


def _has_image_embed(p) -> bool:
    e = _get_embed(p)
    try:
        if e and getattr(e, "$type", "").endswith("embed.images#view"):
            imgs = getattr(e, "images", []) or []
            return len(imgs) > 0
        if e and getattr(e, "$type", "").endswith("embed.recordWithMedia#view"):
            media = getattr(e, "media", None)
            if media and getattr(media, "$type", "").endswith("embed.images#view"):
                imgs = getattr(media, "images", []) or []
                return len(imgs) > 0
    except Exception:
        pass
    return False


def _text_of(p) -> str:
    try:
        rec = getattr(p, "record", None)
        return (getattr(rec, "text", None) or "").lower()
    except Exception:
        return ""


def score_post_for_art(p) -> int:
    score = 0
    if _has_image_embed(p):
        score += 5
    domains = _extract_domains_from_post(p)
    if any(any(md in d for md in MARKET_DOMAINS) for d in domains):
        score += 4
    if any(any(ad in d for ad in ARTICLE_DOMAINS) for d in domains):
        score -= 3
    t = _text_of(p)
    if any(k in t for k in KEYWORDS_GOOD):
        score += 2
    if any(k in t for k in KEYWORDS_BAD):
        score -= 2
    try:
        # Si c'est une réponse ou un repost, on pénalise
        rec = getattr(p, "record", None)
        if rec is not None and getattr(rec, "reply", None) is not None:
            score -= 2
        if getattr(p, "repost", None) is not None:
            score -= 2
    except Exception:
        pass
    return score


def _actor_of(p) -> str:
    try:
        a = getattr(p, "author", None)
        return getattr(a, "handle", "") or ""
    except Exception:
        return ""


def is_original_post(p) -> bool:
    """True si le post n'est PAS une réponse (i.e., post racine).
    Sur Bluesky, l'info est dans record.reply, pas au niveau racine.
    """
    try:
        rec = getattr(p, "record", None)
        if rec is None:
            return False
        # record.reply présent => c'est une réponse
        return getattr(rec, "reply", None) is None
    except Exception:
        return False


def is_from_quote_handle(p) -> bool:
    try:
        a = getattr(p, "author", None)
        handle = getattr(a, "handle", "") or ""
        return handle == QUOTE_HANDLE
    except Exception:
        return False


def pick_latest_original_post_from_actor(client: Client, actor: str, limit: int = 10):
    try:
        feed = get_author_feed_compat(client, actor=actor, limit=limit)
        for item in (getattr(feed, "feed", []) or []):
            # Ignorer les REPOSTS du compte source (item.reason present)
            if getattr(item, "reason", None) is not None:
                continue
            post = getattr(item, "post", None)
            if not post:
                continue
            # S'assurer que le post est bien émis par l'acteur demandé
            try:
                ph = getattr(getattr(post, "author", None), "handle", "") or ""
                if ph != actor:
                    continue
            except Exception:
                continue
            # Strict: uniquement des posts ORIGINAUX AVEC IMAGE
            if is_original_post(post) and _has_image_embed(post):
                return post
    except Exception as e:
        print(f"[pick original err:{actor}] {e}")
    return None

# --- Pipeline ---

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
    try:
        resp = client.send_post(text=text)
        print(f"Original post: {text} -> {getattr(resp, 'uri', '')}")
        # Si on décide d'ajouter un lien, on le met en COMMENTAIRE (reply)
        if random.random() < APPEND_LINK_PROB:
            link = LINK_SITE if random.random() < 0.5 else LINK_OPENSEA
            try:
                client.send_post(
                    text=link,
                    reply_to=M.AppBskyFeedPost.ReplyRef(
                        parent=M.AppBskyFeedPost.ReplyRefParent(uri=getattr(resp, "uri", ""), cid=getattr(resp, "cid", ""))
                    ),
                )
                print(f"Original post link (in reply): {link}")
            except Exception as e:
                print(f"[orig link-reply err] {e}")
    except Exception as e:
        print(f"[post err] {e}")


def repost_from_sources_with_quotes(client: Client, state: Dict[str, Any]):
    if MAX_REPOSTS_PER_RUN <= 0:
        print("Repost cap is 0. Skipping reposts.")
        return

    target_quote_count = max(0, int(round(MAX_REPOSTS_PER_RUN * QUOTE_SHARE)))
    done_reposts = 0
    done_quotes = 0

    # 1) Quote-retweets STRICTEMENT depuis QUOTE_HANDLE et seulement si post ORIGINAL + IMAGE
    while done_quotes < target_quote_count and done_reposts < MAX_REPOSTS_PER_RUN:
        p = pick_latest_original_post_from_actor(client, QUOTE_HANDLE, limit=8)
        if not p or not is_original_post(p) or not is_from_quote_handle(p) or not _has_image_embed(p):
            print("No eligible original image-post from QUOTE_HANDLE to quote.")
            break
        if _uri_recent(state, getattr(p, "uri", "")):
            print("Skip quote: already posted this URI recently.")
            break
        actor = _actor_of(p)
        domains = _extract_domains_from_post(p)
        dom_key = domains[0] if domains else ""
        if not _is_cooled(state.get("recent_sources", []), "actor", actor):
            break
        if dom_key and not _is_cooled(state.get("recent_domains", []), "domain", dom_key):
            break
        q_text, q_link = build_quote_text_and_link()
        ok = safe_quote_repost(client, p.uri, p.cid, q_text, link=q_link)
        if ok:
            _remember_uri(state, getattr(p, "uri", ""))
            done_quotes += 1
            done_reposts += 1
            print(f"Quote-retweet from @{QUOTE_HANDLE}: {p.uri}")
            _record_source_and_domain(state, actor, domains)
            random_sleep()
        else:
            break

    # 2) Reposts simples depuis SOURCE_HANDLES (jamais de phrase/lien ici) — seulement posts ORIGINaux AVEC IMAGE
    sources = [h for h in SOURCE_HANDLES if h and h != QUOTE_HANDLE]
    random.shuffle(sources)
    for actor in sources:
        if done_reposts >= MAX_REPOSTS_PER_RUN:
            break
        try:
            feed = get_author_feed_compat(client, actor=actor, limit=5)
            for item in (getattr(feed, "feed", []) or []):
                # Ignorer les reposts (raison présente) et vérifier l'auteur
                if getattr(item, "reason", None) is not None:
                    continue
                post = getattr(item, "post", None)
                if not post:
                    continue
                try:
                    ph = getattr(getattr(post, "author", None), "handle", "") or ""
                    if ph != actor:
                        continue
                except Exception:
                    continue
                if not is_original_post(post) or not _has_image_embed(post):
                    continue
                if _uri_recent(state, getattr(post, "uri", "")):
                    continue
                domains = _extract_domains_from_post(post)
                dom_key = domains[0] if domains else ""
                if not _is_cooled(state.get("recent_sources", []), "actor", actor):
                    continue
                if dom_key and not _is_cooled(state.get("recent_domains", []), "domain", dom_key):
                    continue
                if score_post_for_art(post) < 1:
                    continue
                if safe_repost(client, post.uri, post.cid):
                    _remember_uri(state, getattr(post, "uri", ""))
                    done_reposts += 1
                    print(f"Repost (simple, image-only) from {actor}: {post.uri}")
                    _record_source_and_domain(state, actor, domains)
                    random_sleep()
                    break
        except Exception as e:
            print(f"[source err:{actor}] {e}")
            continue

    # 3) Complément via discovery (repost simple) avec tri par score + diversité — seulement posts ORIGINAUX AVEC IMAGE
    if done_reposts < MAX_REPOSTS_PER_RUN:
        remaining = MAX_REPOSTS_PER_RUN - done_reposts
        extra = repost_via_discovery(client, state, remaining)
        done_reposts += extra

    print(f"Reposts done: {done_reposts} (quotes={done_quotes}, cap={MAX_REPOSTS_PER_RUN})")


def repost_via_discovery(client: Client, state: Dict[str, Any], remaining_needed: int) -> int:
    if remaining_needed <= 0:
        return 0
    try:
        q = random.choice(_build_queries())
        res = search_posts_compat(client, q=q, limit=40)
        posts = getattr(res, "posts", []) or []
        scored = sorted(posts, key=score_post_for_art, reverse=True)
        used_authors, used_domains = set(), set()
        count = 0
        for p in scored:
            if count >= remaining_needed:
                break
            uri = getattr(p, "uri", None)
            cid = getattr(p, "cid", None)
            if not uri or not cid:
                continue
            # Strict: pas de commentaires/reposts, uniquement ORIGINaux avec IMAGE
            if not is_original_post(p) or not _has_image_embed(p):
                continue
            if _uri_recent(state, uri):
                continue
            actor = _actor_of(p)
            domains = _extract_domains_from_post(p)
            dom_key = domains[0] if domains else ""
            if actor in used_authors:
                continue
            if dom_key and dom_key in used_domains:
                continue
            if not _is_cooled(state.get("recent_sources", []), "actor", actor):
                continue
            if dom_key and not _is_cooled(state.get("recent_domains", []), "domain", dom_key):
                continue
            if score_post_for_art(p) < 2:
                continue
            if safe_repost(client, uri, cid):
                _remember_uri(state, uri)
                count += 1
                used_authors.add(actor)
                if dom_key:
                    used_domains.add(dom_key)
                print(f"Repost via discovery (image-only): {uri}")
                _record_source_and_domain(state, actor, domains)
                random_sleep()
        return count
    except Exception as e:
        print(f"[discovery repost err] {e}")
        return 0


def discovery_likes_and_maybe_reposts(client: Client):
    if random.random() >= DISCOVERY_WEIGHT:
        print("Skip discovery this run (weight check).")
        return
    try:
        q = random.choice(_build_queries())
        res = search_posts_compat(client, q=q, limit=40)
        posts = getattr(res, "posts", []) or []
        random.shuffle(posts)
        likes_done = 0
        for p in posts:
            if likes_done >= DISCOVERY_LIKE_LIMIT:
                break
            # Optionnel: like seulement les posts avec image pour rester dans l'esprit
            if not _has_image_embed(p):
                continue
            uri = getattr(p, "uri", None)
            cid = getattr(p, "cid", None)
            if not uri or not cid:
                continue
            if safe_like(client, uri, cid):
                likes_done += 1
                print(f"Discovery like (image): {uri}")
                random_sleep()
        print(f"Discovery likes done: {likes_done}/{DISCOVERY_LIKE_LIMIT}")
    except Exception as e:
        print(f"[discovery err] {e}")

# --- MAIN ---
if __name__ == "__main__":
    # Garde-fou horaire
    now = _now_local()
    if _is_quiet(now) or not _is_evening(now):
        print(f"Outside window (evening-only). Local time={now.strftime('%Y-%m-%d %H:%M')}. Exit.")
        raise SystemExit(0)

    client = login()

    # 1) Engagements opt-in (mentions/réponses)
    state = load_state()
    engage_opt_in(client, state)

    # 2) Occasionnellement, un post original (reste rare) — liens en commentaire si utilisés
    maybe_original_post(client)

    # 3) Découverte (likes) selon poids
    discovery_likes_and_maybe_reposts(client)

    # 4) Reposts (quotes strictement pour QUOTE_HANDLE, sinon repost simple) —
    #    UNIQUEMENT posts originaux avec IMAGES. Liens en commentaire.
    repost_from_sources_with_quotes(client, state)

    print("Bot2 run completed (evening-only, cooldowns, no duplicates, images-only, links-in-replies).")
