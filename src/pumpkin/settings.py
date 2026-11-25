from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PUMPKIN_", validate_assignment=True
    )

    timeout: float = 2.5
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    subdomains_api_url: str = "https://ip.thc.org/api/v1/lookup/subdomains"
    subdomains_page_size: int = 100
    titus_binary: str = "titus"
    titus_timeout: float = 300.0
    titus_include_noisy: bool = True
    output_limit: int = 300
    download_chunk_size: int = 65536


settings = Settings()
