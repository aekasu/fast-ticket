from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integration.osticket import read_repository as repo
from app.modules.settings.models import AttachmentLimits


async def attachment_limits(session: AsyncSession) -> AttachmentLimits:
    osticket = await repo.get_max_file_size(session)
    api = settings.ATTACHMENT_MAX_FILE_BYTES
    return AttachmentLimits(
        max_file_bytes=min(osticket, api) if osticket else api,
        osticket_max_file_bytes=osticket,
        api_max_file_bytes=api,
        max_files=settings.ATTACHMENT_MAX_FILES,
        max_total_bytes=settings.ATTACHMENT_MAX_TOTAL_BYTES,
    )
