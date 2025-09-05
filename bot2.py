# bot2.py
import os
import random
import time
from atproto import Client, models as M

# --- Secrets GitHub ---
HANDLE = os.getenv("BSKY2_HANDLE")
APP_PASSWORD = os.getenv("BSKY2_APP_PASSWORD")
if not HANDLE or not APP_PASSWORD:
    raise SystemExit("Manque BSKY2_HANDLE ou BSKY2_APP_PASSWORD (Secrets GitHub).")

# --- Réglages (Variables GitHub) ---
QUERY = os.getenv("BOT2_QUERY", "art OR \"digital art\" OR nft")
DISCOVERY_WEIGHT = float(os.getenv("BOT2_DISCOVERY_WEIGHT", "0.7"))   # 70% découverte (likes/reposts sur recherche)
LIKE_LIMIT = int(os.getenv("BOT2_LIKE_LIMIT", "3"))
REPOST_LIMIT = int(os.getenv("BOT2_REPOST_LIMIT", "1"))
SOURCE_HANDLES = [h.strip() for h in os.getenv("BOT2_SOURCE_HANDLES", "").split(",") if h.strip()]

# --- Posts originaux (déjà existant) ---
DO_ORIGINAL_POST_WEIGHT = float(os.getenv("BOT2_ORIGINAL_POST_WEIGHT", "0.25"))  # 25% des runs ≈ post original
ORIGINAL_POSTS = [
    "Exploring stories in color and motion 🎨✨",
    "Fiction painted in pixels. More soon.",
    "Sketches from my universe of art & fiction.",
    "Digital brushstrokes, narrative vibes.",
    "Sharing what I love: art, fiction, and a bit of NFTs.",
]
LINK_SITE = os.getenv("BOT2_LINK_SITE", "https://louphi1987.github.io/Site_de_Louphi/")
LINK_OPENSEA = os.getenv("BOT2_LINK_OPENSEA", "https://opensea.io/collection/loufis-art")
APPEND_LINK_PROB = float(os.getenv("BOT2_APPEND_LINK_PROB", "0.5"))  # 50% des posts originaux ajoutent un lien

# --- NOUVEAU : Promo Pictnart (≈10% du temps) ---
PICTNART_WEIGHT = float(os.getenv("BOT2_PICTNART_WEIGHT", "0.10"))  # 10% du temps un post dédié Pictnart
PICTNART_LINK = os.getenv("BOT2_PICTNART_LINK", "https://pictnartcompany-ux.github.io/grow_your_craft/")
PICTNART_LINES = [
    "Partner spotlight: Pictnart helps artists grow their craft 🚀 Check it out:",
    "If you're an artist, Pictnart is a neat boost for your workflow ⚡ Learn more:",
    "Level up your art journey with Pictnart — tools made for creators ✨",
    "Pictnart supports artists with smart tools and guidance 🤝 Discover:",
    "Creators: Pictnart can simplify your process and keep you moving 🎯",
    "I partner with Pictnart to help artists thrive — worth a look 👀",
]
PICTNART_EMOJIS = ["🎨", "✨", "🚀", "💡", "🧰", "🛠️", "🧭", "🌟", "🤝", "🎯"]

def login() -> Client:
    c = Client()
    c.login(HANDLE, APP_PASSWORD)
    return c

def discover_and_engage(client: Client):
    liked = 0
    reposted = 0
    res = client.app.bsky.feed.search_posts(q=QUERY, limit=30, sort="latest")
    posts = list(res.posts or [])
    random.shuffle(posts)
    for post in posts:
        uri, cid = getattr(post, "uri", None), getattr(post, "cid", None)
        if not uri or not cid:
            continue

        # Like
        if liked < LIKE_LIMIT:
            try:
                client.like(uri=uri, cid=cid)
                liked += 1
                print(f"Like: {uri}")
                time.sleep(1.0)
            except Exception as e:
                print(f"[like err] {e}")

        # Repost
        if reposted < REPOST_LIMIT:
            try:
                client.repost(uri=uri, cid=cid)
                reposted += 1
                print(f"Repost: {uri}")
                time.sleep(1.0)
            except Exception as e:
                print(f"[repost err] {e}")

        if liked >= LIKE_LIMIT and reposted >= REPOST_LIMIT:
            break

def repost_from_sources(client: Client):
    if not SOURCE_HANDLES:
        print("No SOURCE_HANDLES provided, skipping source reposts.")
        return
    reposted = 0
    for actor in SOURCE_HANDLES:
        try:
            feed = client.app.bsky.feed.get_author_feed(actor=actor, limit=5)
            for item in (feed.feed or []):
                post = getattr(item, "post", None)
                # Reposter uniquement des posts originaux (pas replies/reposts)
                if not post or (post.reply is not None) or (post.repost is not None):
                    continue
                client.repost(uri=post.uri, cid=post.cid)
                reposted += 1
                print(f"Repost from {actor}: {post.uri}")
                time.sleep(1.0)
                break  # un repost max par source
            if reposted >= REPOST_LIMIT:
                break
        except Exception as e:
            print(f"[source err:{actor}] {e}")
            continue

def maybe_pictnart_post(client: Client) -> bool:
    """10% du temps, poste un message promo Pictnart (avec lien + emojis). Retourne True si posté."""
    if random.random() >= PICTNART_WEIGHT:
        print("Skip Pictnart promo this run.")
        return False
    base = random.choice(PICTNART_LINES)
    # Ajoute 1–2 emojis aléatoires à la fin
    emjs = " " + " ".join(random.sample(PICTNART_EMOJIS, k=random.choice([1, 2])))
    text = f"{base} {PICTNART_LINK}{emjs}"
    try:
        resp = client.send_post(text=text)
        print(f"Pictnart post: {text} -> {getattr(resp, 'uri', '')}")
        return True
    except Exception as e:
        print(f"[pictnart post err] {e}")
        return False

def maybe_original_post(client: Client):
    """Publie un post original simple de temps en temps (si pas de Pictnart ce run)."""
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

if __name__ == "__main__":
    client = login()

    # 1) Tentative de post Pictnart (10% par défaut)
    did_pictnart = maybe_pictnart_post(client)

    # 2) Si pas de post Pictnart cette fois, on tente un post original (25% par défaut)
    if not did_pictnart:
        maybe_original_post(client)

    # 3) Ensuite, soit découverte/engage, soit repost depuis sources
    if random.random() < DISCOVERY_WEIGHT:
        discover_and_engage(client)
    else:
        repost_from_sources(client)

    print("Bot2 run completed.")
