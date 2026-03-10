"""
Configuration management using Dynaconf.
Loads settings from settings.yaml and secrets from .env
"""

from dynaconf import Dynaconf

settings = Dynaconf(
    envvar_prefix="FLICKR_INDEX",
    settings_files=["settings.yaml"],
    load_dotenv=True,
    dotenv_path=".env",
)
