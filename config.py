from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "AutoNews AI"
    env: str = "development"
    secret_key: str = "change-this-secret"
    database_url: str = "sqlite:///./data/autonews.db"
    admin_username: str = "admin"
    admin_password: str = "change-me"
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model: str = ""
    auto_publish_score: int = 90
    podcast_auto_generate: bool = True
    scheduler_minutes: int = 10
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
