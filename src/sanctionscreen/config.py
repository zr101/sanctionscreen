"""Configuration: code defaults <- config/default.toml <- SANCTIONSCREEN_* env vars.

Model defaults mirror config/default.toml, so the service still runs if the
TOML file is absent (e.g. installed as a bare package). Environment variables
win over the TOML file; nesting uses "__", e.g.
SANCTIONSCREEN_SCORING__DEFAULT_THRESHOLD=80.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from sanctionscreen.normalise import DEFAULT_HONORIFICS


class DatabaseConfig(BaseModel):
    path: Path = Path("data/sanctions.db")


class DfatSource(BaseModel):
    url: str = (
        "https://www.dfat.gov.au/sites/default/files/Australian_Sanctions_Consolidated_List.xlsx"
    )


class UnSource(BaseModel):
    url: str = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"


class OfacSource(BaseModel):
    sdn_url: str = "https://sanctionslistservice.ofac.treas.gov/api/download/sdn.csv"
    alt_url: str = "https://sanctionslistservice.ofac.treas.gov/api/download/alt.csv"
    add_url: str = "https://sanctionslistservice.ofac.treas.gov/api/download/add.csv"
    comments_url: str = (
        "https://sanctionslistservice.ofac.treas.gov/api/download/sdn_comments.csv"
    )


class SourcesConfig(BaseModel):
    dfat: DfatSource = DfatSource()
    un: UnSource = UnSource()
    ofac: OfacSource = OfacSource()


class NormalisationConfig(BaseModel):
    honorifics: list[str] = list(DEFAULT_HONORIFICS)


class ScoringConfig(BaseModel):
    strategy: str = "max"
    weight_exact: float = 1.0
    weight_fuzzy: float = 0.97
    weight_phonetic: float = 0.90
    weight_embedding: float = 0.85
    corroboration_bonus: float = 3.0
    corroboration_floor: float = 80.0
    corroboration_min_layers: int = 2
    score_cap: float = 99.0
    fuzzy_token_sort_weight: float = 0.7
    fuzzy_partial_weight: float = 0.3
    fuzzy_cutoff: float = 60.0
    fuzzy_candidate_limit: int = 64
    embedding_cosine_cutoff: float = 0.45
    embedding_candidate_limit: int = 64
    default_threshold: float = 75.0
    default_max_results: int = 10


class EmbeddingConfig(BaseModel):
    enabled: bool = True
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    vectors_path: Path = Path("data/embeddings.db")
    precompute_on_startup: bool = True


def _config_file() -> Path | None:
    env = os.environ.get("SANCTIONSCREEN_CONFIG")
    if env:
        return Path(env)
    for base in (Path.cwd(), Path(__file__).resolve().parents[2]):
        candidate = base / "config" / "default.toml"
        if candidate.is_file():
            return candidate
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SANCTIONSCREEN_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    database: DatabaseConfig = DatabaseConfig()
    sources: SourcesConfig = SourcesConfig()
    normalisation: NormalisationConfig = NormalisationConfig()
    scoring: ScoringConfig = ScoringConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        toml_file = _config_file()
        if toml_file is not None:
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=toml_file))
        return tuple(sources)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
