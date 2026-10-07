from typing import Optional

from pydantic import BaseModel

from app.core.timezone import Instant


class PluginInstance(BaseModel):
    id: int
    name: str
    is_enabled: bool


class Plugin(BaseModel):
    id: int
    name: str
    version: Optional[str] = None
    install_path: str
    is_phar: bool
    is_active: bool
    installed: Instant
    instances: list[PluginInstance]


class PluginList(BaseModel):
    plugins: list[Plugin]
