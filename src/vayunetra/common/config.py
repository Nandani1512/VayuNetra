from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = Field(
        default="postgresql+psycopg://vayunetra:vayunetra_dev_change_me@localhost:5432/vayunetra"
    )

    # MinIO / S3
    minio_endpoint: str = "localhost:9000"
    minio_bucket: str = "vayunetra"
    aws_access_key_id: str = "minioadmin"
    aws_secret_access_key: str = "minioadmin_dev_change_me"
    mlflow_s3_endpoint_url: str = "http://localhost:9000"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # MLflow
    mlflow_tracking_uri: str = "http://localhost:5000"

    # Prefect
    prefect_api_url: str = "http://localhost:4200/api"

    # API keys
    openaq_api_key: str = ""
    firms_api_key: str = ""
    cams_ads_key: str = ""
    cams_ads_url: str = "https://ads.atmosphere.copernicus.eu/api/v2"

    # GEE
    gee_service_account: str = ""
    gee_key_file: str = ""

    # LLM
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Bots
    telegram_token: str = ""

    # Auth
    jwt_secret: str = "change_me_in_prod_32_chars_min_abcdefgh"
    jwt_algorithm: str = "HS256"
    jwt_expires_min: int = 60

    # Flags
    enable_langgraph: bool = True
    demo_mode: bool = False
    log_level: str = "INFO"

    # Observability
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "vayunetra-api"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
