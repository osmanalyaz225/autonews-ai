import hashlib, re, html, json
from datetime import datetime
import feedparser, httpx
from sqlalchemy import select
from .config import settings
from .models import Source, Article, Podcast

CATEGORIES = ["Gündem","Dünya","Ekonomi","Spor","Teknoloji","Sağlık","Kültür","Bilim"]
DEFAULT_FEEDS = [
    ("Google News Türkiye","https://news.google.com/rss?hl=tr&gl=TR&ceid=TR:tr"),
    ("Google News Dünya","https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"),
    ("Google News Ekonomi","https://news.google.com/rss/search?q=ekonomi&hl=tr&gl=TR&ceid=TR:tr"),
    ("Google News Teknoloji","https://news.google.com/rss/search?q=teknoloji&hl=tr&gl=TR&ceid=TR:tr"),
    ("Google News Spor","https://news.google.com/rss/search?q=spor&hl=tr&gl=TR&ceid=TR:tr"),
]

def slugify(text):
    t=text.lower().translate(str.maketrans("çğıöşü","cgiosu"))
    t=re.sub(r"[^a-z0-9]+","-",t).strip("-")
    return t[:150] or hashlib.sha1(text.encode()).hexdigest()[:12]

def clean(text):
    return re.sub(r"\s+"," ",html.unescape(re.sub("<[^>]+>"," ",text or ""))).strip()

def guess_category(text):
    t=text.lower()
    groups={"Ekonomi":["ekonomi","borsa","faiz","dolar","euro","enflasyon","şirket"],
            "Spor":["futbol","basketbol","spor","maç","lig","transfer"],
            "Teknoloji":["yapay zeka","teknoloji","yazılım","telefon","çip"],
            "Sağlık":["sağlık","hastane","ilaç","kanser","doktor"],
            "Bilim":["bilim","uzay","araştırma","bilim insanı"],
            "Kültür":["kültür","sanat","sinema","müzik","kitap"],
            "Dünya":["abd","avrupa","rusya","ukrayna","gaza","israil","dünya"]}
    for cat, words in groups.items():
        if any(w in t for w in words): return cat
    return "Gündem"

def seed_sources(db):
    for name,url in DEFAULT_FEEDS:
        if not db.scalar(select(Source).where(Source.url==url)):
            db.add(Source(name=name,url=url))
    db.commit()

def extract_image_url(item):
    candidates=[]
    for key in ("media_content","media_thumbnail"):
        for media in (item.get(key) or []):
            if isinstance(media, dict) and media.get("url"):
                candidates.append(media["url"])
    for enc in (item.get("enclosures") or []):
        if isinstance(enc, dict) and enc.get("href") and str(enc.get("type","")).startswith("image/"):
            candidates.append(enc["href"])
    text=item.get("summary","") or item.get("description","") or ""
    m=re.search(r'<img[^>]+src=["\']([^"\']+)["\']', text, re.I)
    if m: candidates.append(m.group(1))
    return next((u for u in candidates if str(u).startswith(("http://","https://"))), "")

def collect_feeds(db):
    created=[]
    sources=db.scalars(select(Source).where(Source.enabled==True)).all()
    used_slugs=set(db.scalars(select(Article.slug)).all())
    for source in sources:
        try:
            feed=feedparser.parse(source.url)
            for item in feed.entries[:40]:
                title=clean(item.get("title","")); url=item.get("link","")
                if not title or not url: continue
                if db.scalar(select(Article).where(Article.source_url==url)): continue
                key=hashlib.sha256(url.encode()).hexdigest()[:10]
                base_slug=slugify(title)+"-"+key
                slug=base_slug
                suffix=2
                while slug in used_slugs:
                    slug=f"{base_slug}-{suffix}"
                    suffix += 1
                used_slugs.add(slug)
                a=Article(title=title,slug=slug,
                          summary=clean(item.get("summary",""))[:1800],
                          body=clean(item.get("summary",""))[:1800],
                          category=guess_category(title),source_name=source.name,
                          source_url=url,source_count=1,confidence=60,status="published",
                          image_url=extract_image_url(item),
                          published=True,published_at=datetime.utcnow())
                db.add(a); created.append(a)
        except Exception as exc:
            print(f"feed error {source.url}: {exc}")
    db.commit()
    return created

async def ai_complete(prompt):
    if not (settings.ai_base_url and settings.ai_api_key and settings.ai_model): return ""
    url=settings.ai_base_url.rstrip("/")+"/chat/completions"
    payload={"model":settings.ai_model,"messages":[
        {"role":"system","content":"Türkçe haber editörüsün. Gerçek olmayan bilgi üretme. Kaynakta olmayan ayrıntı ekleme. Belirsizliği ve atfı koru."},
        {"role":"user","content":prompt}],"temperature":0.2}
    async with httpx.AsyncClient(timeout=60) as client:
        r=await client.post(url,headers={"Authorization":f"Bearer {settings.ai_api_key}"},json=payload)
        r.raise_for_status(); return r.json()["choices"][0]["message"]["content"]

async def enrich_article(article):
    prompt=f"""Aşağıdaki haberi profesyonel Türkçe haber formatında düzenle.
Başlık: {article.title}
Özet: {article.summary}
Kaynak: {article.source_name}
Kaynak URL: {article.source_url}
Yalnızca verilen bilgileri kullan. JSON döndür: title, summary, body, category, confidence, tags."""
    raw=await ai_complete(prompt)
    if not raw: return article
    try:
        data=json.loads(raw[raw.find("{"):raw.rfind("}")+1])
        article.title=data.get("title",article.title)[:500]
        article.summary=data.get("summary",article.summary)
        article.body=data.get("body",article.body)
        article.category=data.get("category",article.category)
        article.confidence=max(float(data.get("confidence",article.confidence)),article.confidence)
        article.tags=", ".join(data.get("tags",[])) if isinstance(data.get("tags",[]),list) else str(data.get("tags",""))
    except Exception:
        article.body=raw
        article.summary=raw[:500]
        article.confidence=max(article.confidence,75)
    return article

async def generate_article_image(article):
    base = settings.image_base_url or settings.ai_base_url
    key = settings.image_api_key or settings.ai_api_key
    model = settings.image_model
    if not (base and key and model): return ""
    prompt = "Create a professional editorial news cover image for a Turkish news website. Photorealistic, modern newsroom quality, no logos, no watermarks, no readable text. Topic: " + article.title + ". Category: " + article.category
    url = base.rstrip("/") + "/images/generations"
    payload = {"model": model, "prompt": prompt, "size": settings.image_size, "n": 1}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, headers={"Authorization": "Bearer " + key}, json=payload)
            r.raise_for_status()
            data = r.json().get("data", [])
            if data and data[0].get("url"):
                article.image_url = data[0]["url"]
                return article.image_url
    except Exception as exc:
        print("image generation error for article %s: %s" % (article.id, exc))
    return ""
async def make_podcast(db,max_articles=8):
    articles=db.scalars(select(Article).where(Article.published==True).order_by(Article.published_at.desc()).limit(max_articles)).all()
    if not articles: return None
    lines=["Merhaba, AutoNews günlük haber bültenine hoş geldiniz.","Bugünün öne çıkan gelişmeleri:"]
    lines += [f"{i}. {a.title}. {a.summary}" for i,a in enumerate(articles,1)]
    lines.append("Bizi takip ettiğiniz için teşekkürler.")
    p=Podcast(title=f"Günün Haberleri — {datetime.utcnow():%Y-%m-%d}",
              description="AutoNews günlük otomatik haber podcasti",
              script="\n\n".join(lines),published=True)
    db.add(p); db.commit(); return p
