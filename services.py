import hashlib, re, html
from datetime import datetime
from urllib.parse import urlparse
import feedparser, httpx
from sqlalchemy import select
from .config import settings
from .models import Source, Article, Podcast

DEFAULT_FEEDS = [
    ("Google News TR", "https://news.google.com/rss?hl=tr&gl=TR&ceid=TR:tr"),
    ("Google News World", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"),
]


def slugify(text):
    t = text.lower()
    table = str.maketrans("çğıöşü", "cgiosu")
    t = t.translate(table)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:160] or hashlib.sha1(text.encode()).hexdigest()[:12]


def clean(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", text or ""))).strip()


def seed_sources(db):
    for name, url in DEFAULT_FEEDS:
        if not db.scalar(select(Source).where(Source.url == url)):
            db.add(Source(name=name, url=url))
    db.commit()


def collect_feeds(db):
    sources = db.scalars(select(Source).where(Source.enabled == True)).all()
    created = []
    for source in sources:
        try:
            feed = feedparser.parse(source.url)
            for item in feed.entries[:30]:
                title = clean(item.get("title", "")).strip()
                url = item.get("link", "")
                if not title or not url:
                    continue
                # URL hash is the safest low-cost duplicate guard.
                key = hashlib.sha256(url.encode()).hexdigest()[:24]
                slug = slugify(title) + "-" + key[:8]
                if db.scalar(select(Article).where(Article.slug == slug)):
                    continue
                summary = clean(item.get("summary", ""))[:1500]
                article = Article(title=title, slug=slug, summary=summary,
                                  body=summary, category="Gündem", source_name=source.name,
                                  source_url=url, source_count=1, confidence=60,
                                  status="review")
                db.add(article); created.append(article)
        except Exception as exc:
            print(f"feed error {source.url}: {exc}")
    db.commit()
    return created

async def ai_complete(prompt: str) -> str:
    if not settings.ai_base_url or not settings.ai_api_key or not settings.ai_model:
        return ""
    url = settings.ai_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
    payload = {"model": settings.ai_model, "messages": [{"role": "system", "content": "You are a careful news editor. Never invent facts. Preserve uncertainty and attribution."}, {"role": "user", "content": prompt}], "temperature": 0.2}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=payload); r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def enrich_article(article):
    prompt = f"""Edit this news item. Return concise Turkish article copy. Do not add facts not present.\nTITLE: {article.title}\nSUMMARY: {article.summary}\nSOURCE: {article.source_name}\nSOURCE URL: {article.source_url}\nReturn: TITLE, SUMMARY, CATEGORY, BODY, CONFIDENCE (0-100)."""
    result = await ai_complete(prompt)
    if result:
        article.body = result
        article.summary = result[:500]
        article.confidence = max(article.confidence, 80)
    return article

async def make_podcast(db, max_articles=8):
    articles = db.scalars(select(Article).where(Article.published == True).order_by(Article.published_at.desc()).limit(max_articles)).all()
    if not articles:
        return None
    lines = ["Merhaba, AutoNews günlük haber bültenine hoş geldiniz.", "Bugünün öne çıkan gelişmeleri şöyle:"]
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. {a.title}. {a.summary}")
    lines.append("Bizi takip ettiğiniz için teşekkürler. Bir sonraki bültende görüşmek üzere.")
    script = "\n\n".join(lines)
    p = Podcast(title=f"Günün Haberleri — {datetime.utcnow():%Y-%m-%d}", description="AutoNews günlük otomatik haber podcasti", script=script, published=True)
    db.add(p); db.commit(); return p
