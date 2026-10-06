from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from apscheduler.schedulers.background import BackgroundScheduler
from .db import SessionLocal, init_db
from .models import Article, Source
from .services import seed_sources, collect_feeds, enrich_article, make_podcast
import asyncio

BASE = Path(__file__).resolve().parent
scheduler = BackgroundScheduler()

def job():
    db = SessionLocal()
    try:
        new = collect_feeds(db)
        for a in new[:10]:
            asyncio.run(enrich_article(a))
            if a.confidence >= 90:
                a.published = True; a.status = "published"; a.published_at = datetime.utcnow()
        db.commit()
        if new:
            # Podcast generation is intentionally lightweight; audio TTS is an extension point.
            asyncio.run(make_podcast(db))
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app):
    init_db(); db=SessionLocal(); seed_sources(db); db.close()
    scheduler.add_job(job, "interval", minutes=10, id="news-pipeline", replace_existing=True)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)

app = FastAPI(title="AutoNews AI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db=SessionLocal(); articles=db.scalars(select(Article).where(Article.published==True).order_by(desc(Article.published_at)).limit(20)).all(); db.close()
    return templates.TemplateResponse("home.html", {"request":request,"articles":articles})

@app.get("/haber/{slug}", response_class=HTMLResponse)
def article_page(request: Request, slug: str):
    db=SessionLocal(); a=db.scalar(select(Article).where(Article.slug==slug)); db.close()
    if not a: return HTMLResponse("Bulunamadı", status_code=404)
    return templates.TemplateResponse("article.html", {"request":request,"article":a})

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    db=SessionLocal(); articles=db.scalars(select(Article).order_by(desc(Article.created_at)).limit(100)).all(); db.close()
    return templates.TemplateResponse("admin.html", {"request":request,"articles":articles})

@app.post("/admin/article/{article_id}/publish")
def publish(article_id:int):
    db=SessionLocal(); a=db.get(Article,article_id)
    if a: a.published=True; a.status="published"; a.published_at=datetime.utcnow(); db.commit()
    db.close(); return RedirectResponse("/admin", status_code=303)

@app.post("/admin/run")
def run_now():
    job(); return RedirectResponse("/admin", status_code=303)

@app.get("/rss.xml")
def rss(request: Request):
    db=SessionLocal(); articles=db.scalars(select(Article).where(Article.published==True).order_by(desc(Article.published_at)).limit(30)).all(); db.close()
    from xml.sax.saxutils import escape
    base = str(request.base_url).rstrip("/")
    items=[]
    for a in articles:
        items.append(f"<item><title>{escape(a.title)}</title><link>{base}/haber/{a.slug}</link><description>{escape(a.summary)}</description><pubDate>{a.published_at}</pubDate></item>")
    xml=f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>AutoNews AI</title><link>{base}</link><description>Otomatik haber akışı</description>'+''.join(items)+'</channel></rss>'
    return Response(xml, media_type="application/rss+xml")

@app.get("/podcast.xml")
def podcast_rss(request: Request):
    db=SessionLocal(); rows=db.execute(__import__('sqlalchemy').text("SELECT title, description, created_at FROM podcasts WHERE published=1 ORDER BY created_at DESC LIMIT 30")).fetchall(); db.close()
    from xml.sax.saxutils import escape
    base = str(request.base_url).rstrip("/")
    items=''.join(f"<item><title>{escape(r[0])}</title><description>{escape(r[1])}</description><pubDate>{r[2]}</pubDate></item>" for r in rows)
    xml=f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>AutoNews Podcast</title><link>{base}</link><description>Günlük haber podcasti</description>'+items+'</channel></rss>'
    return Response(xml, media_type="application/rss+xml")

@app.get("/health")
def health(): return {"status":"ok","service":"autonews-ai"}
