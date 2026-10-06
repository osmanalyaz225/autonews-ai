from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from apscheduler.schedulers.background import BackgroundScheduler
from .db import SessionLocal, init_db
from .models import Article
from .services import seed_sources, collect_feeds, enrich_article, ensure_article_image, make_podcast

BASE=Path(__file__).resolve().parent
scheduler=BackgroundScheduler()

async def backfill_articles(limit=50):
    """Repair older published articles missing full content or images."""
    db=SessionLocal()
    repaired=0
    try:
        articles=db.scalars(
            select(Article).where(
                Article.published==True
            ).order_by(Article.created_at.asc()).limit(limit)
        ).all()
        from .services import hydrate_article, generate_article_image
        for article in articles:
            changed=False
            if not article.body or len((article.body or "").strip()) < 500 or not article.image_url:
                await hydrate_article(article)
                changed=True
            if not article.image_url:
                await ensure_article_image(article)
                changed=True
            if changed:
                repaired += 1
        db.commit()
        return repaired
    except Exception as exc:
        db.rollback()
        print(f"backfill error: {exc}")
        return repaired
    finally:
        db.close()

async def async_job():
    db=SessionLocal()
    try:
        repaired=await backfill_articles(50)
        print(f"backfill repaired: {repaired}")
        new=collect_feeds(db)
        for article in new[:20]:
            from .services import hydrate_article
            await hydrate_article(article)
            await enrich_article(article)
            if not article.image_url:
                await generate_article_image(article)
            if article.confidence>=90:
                article.published=True
                article.status="published"
                article.published_at=datetime.utcnow()
        db.commit()
        if new:
            await make_podcast(db)
        return len(new)
    except Exception as exc:
        db.rollback()
        print(f"pipeline error: {exc}")
        return 0
    finally:
        db.close()

def job():
    return asyncio.run(async_job())

@asynccontextmanager
async def lifespan(app):
    BASE.joinpath("data").mkdir(exist_ok=True)
    init_db()
    db=SessionLocal()
    try: seed_sources(db)
    finally: db.close()
    await async_job()
    scheduler.add_job(job,"interval",minutes=10,id="news-pipeline",replace_existing=True)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)

app=FastAPI(title="AutoNews AI",lifespan=lifespan)
app.mount("/static",StaticFiles(directory=str(BASE/"static")),name="static")
templates=Jinja2Templates(directory=str(BASE/"templates"))

@app.get("/",response_class=HTMLResponse)
def home(request:Request):
    db=SessionLocal()
    try: articles=db.scalars(select(Article).where(Article.published==True).order_by(desc(Article.published_at)).limit(50)).all()
    finally: db.close()
    return templates.TemplateResponse("home.html",{"request":request,"articles":articles})

@app.get("/haber/{slug}",response_class=HTMLResponse)
def article_page(request:Request,slug:str):
    db=SessionLocal()
    try: article=db.scalar(select(Article).where(Article.slug==slug))
    finally: db.close()
    if not article: return HTMLResponse("Haber bulunamadı",404)
    return templates.TemplateResponse("article.html",{"request":request,"article":article})

@app.get("/admin",response_class=HTMLResponse)
def admin(request:Request):
    db=SessionLocal()
    try: articles=db.scalars(select(Article).order_by(desc(Article.created_at)).limit(100)).all()
    finally: db.close()
    return templates.TemplateResponse("admin.html",{"request":request,"articles":articles})

@app.post("/admin/article/{article_id}/publish")
def publish(article_id:int):
    db=SessionLocal()
    try:
        a=db.get(Article,article_id)
        if a:
            a.published=True; a.status="published"; a.published_at=datetime.utcnow(); db.commit()
    finally: db.close()
    return RedirectResponse("/admin",303)

@app.post("/admin/run")
def run_now():
    job()
    return RedirectResponse("/admin",303)

@app.get("/rss.xml")
def rss(request:Request):
    db=SessionLocal()
    try: articles=db.scalars(select(Article).where(Article.published==True).order_by(desc(Article.published_at)).limit(30)).all()
    finally: db.close()
    from xml.sax.saxutils import escape
    base=str(request.base_url).rstrip("/")
    items="".join(f"<item><title>{escape(a.title)}</title><link>{base}/haber/{a.slug}</link><description>{escape(a.summary)}</description><pubDate>{a.published_at}</pubDate></item>" for a in articles)
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>AutoNews AI</title><link>{base}</link><description>Otomatik haber akışı</description>{items}</channel></rss>',media_type="application/rss+xml")

@app.get("/podcast.xml")
def podcast_rss(request:Request):
    db=SessionLocal()
    try: rows=db.execute(__import__("sqlalchemy").text("SELECT title,description,created_at FROM podcasts WHERE published=1 ORDER BY created_at DESC LIMIT 30")).fetchall()
    finally: db.close()
    from xml.sax.saxutils import escape
    base=str(request.base_url).rstrip("/")
    items="".join(f"<item><title>{escape(r[0])}</title><description>{escape(r[1])}</description><pubDate>{r[2]}</pubDate></item>" for r in rows)
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>AutoNews Podcast</title><link>{base}</link><description>Günlük haber podcasti</description>{items}</channel></rss>',media_type="application/rss+xml")

@app.get("/health")
def health(): return {"status":"ok","service":"autonews-ai","automation":True}
