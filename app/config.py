"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from typing import List, Union

from typing_extensions import Annotated
from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Server ----
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_reload: bool = True
    log_level: str = "info"

    # ---- Camoufox / stealth browser ----
    browser_headless: Union[bool, str] = False
    browser_humanize: Union[bool, float] = True
    browser_geoip: Union[bool, str] = True
    # NoDecode: keep the raw env string ("windows,macos,linux") out of the
    # JSON decoder so our `_split_os` validator below can split it.
    browser_os: Annotated[List[str], NoDecode] = ["windows", "macos", "linux"]
    browser_locale: str = "en-US"
    browser_block_webrtc: bool = True
    browser_block_images: bool = False
    browser_proxy: str = ""
    browser_max_concurrency: int = 4
    browser_nav_timeout_ms: int = 45000

    @field_validator("browser_os", mode="before")
    @classmethod
    def _split_os(cls, v: object) -> object:
        """Allow a comma-separated string in the env file."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("browser_headless", "browser_humanize", "browser_geoip", mode="before")
    @classmethod
    def _coerce_special(cls, v: object) -> object:
        """These accept bool OR a special value (e.g. 'virtual', a float, an IP)."""
        if isinstance(v, str):
            low = v.strip().lower()
            if low in {"true", "1", "yes"}:
                return True
            if low in {"false", "0", "no", ""}:
                return False
            # otherwise keep the raw string ('virtual', an IP, '1.5', ...)
            try:
                return float(v)
            except ValueError:
                return v
        return v

    def camoufox_kwargs(self) -> dict:
        """Build the keyword arguments passed to AsyncCamoufox()."""
        kwargs: dict = {
            "headless": self.browser_headless,
            "humanize": self.browser_humanize,
            "geoip": self.browser_geoip,
            "os": self.browser_os,
            "locale": self.browser_locale,
            "block_webrtc": self.browser_block_webrtc,
            "block_images": self.browser_block_images,
        }
        if self.browser_proxy:
            kwargs["proxy"] = {"server": self.browser_proxy}
        return kwargs


settings = Settings()
