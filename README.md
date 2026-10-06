# AutoNews AI — Otonom Haber + Podcast Platformu

Bu proje haber kaynaklarından içerik toplayan, tekrarları ayıklayan, AI ile taslak haber/podcast üreten ve web üzerinde yayınlayan çalışır bir başlangıç platformudur.

## Özellikler
- FastAPI backend
- Modern responsive haber sitesi
- Admin paneli
- PostgreSQL desteği (SQLite varsayılan)
- RSS haber toplama
- Duplicate/benzer haber tespiti
- AI editör entegrasyonu (OpenAI-compatible API)
- Kaynak/güven skoru alanları
- Otomatik podcast senaryosu
- TTS için OpenAI-compatible entegrasyon noktası
- Podcast RSS feed
- Haber RSS feed
- Background scheduler
- Docker Compose
- Sağlık kontrolü ve temel test

## Hızlı başlatma
```bash
cp .env.example .env
docker compose up --build
```
Sonra: http://localhost:8000
Admin: http://localhost:8000/admin

Varsayılan demo admin: admin / change-me

## AI
`.env` içinde `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL` doldurulursa AI üretimi etkinleşir. Anahtar yoksa sistem deterministik fallback metinleri üretir; böylece uygulama yine çalışır.

## Üretim notu
- `SECRET_KEY`, admin parolası ve veritabanı parolası mutlaka değiştirilmeli.
- Haberlerin otomatik yayınlanması yerine yüksek riskli kategoriler için insan onayı kullanılması önerilir.
- Telifli görselleri izinsiz yeniden yayınlamayın.
- Resmi mevzuat ve yayıncılık yükümlülüklerini yayın öncesinde hukuk uzmanıyla kontrol edin.
