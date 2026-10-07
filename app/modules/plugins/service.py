from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.modules.plugins.models import Plugin, PluginInstance, PluginList


async def list_plugins(session: AsyncSession) -> PluginList:
    instances: dict[int, list[PluginInstance]] = {}
    for row in map(dict, await repo.list_plugin_instances(session)):
        instances.setdefault(row["plugin_id"], []).append(PluginInstance(id=row["id"], name=row["name"], is_enabled=bool(row["flags"] & 1)))
    return PluginList(plugins=[
        Plugin(id=r["id"], name=r["name"], version=r["version"], install_path=r["install_path"], is_phar=bool(r["isphar"]),
               is_active=bool(r["isactive"]), installed=r["installed"], instances=instances.get(r["id"], []))
        for r in map(dict, await repo.list_plugins(session))])
