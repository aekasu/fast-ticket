"""``python -m app.doctor`` — preflight check of the osTicket instance this API points at.

Exit status is 1 if any check FAILED (warnings don't fail it), so it can gate a deploy:

    make doctor        # inside the running api container
"""

import asyncio
import sys

from app.core.config import settings
from app.core.db import AsyncSessionLocal, engine
from app.integration.osticket.doctor import FAIL, OK, WARN, run_checks

_MARK = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}


async def main() -> int:
    url = engine.url.render_as_string(hide_password=True)
    print(f"fast-ticket doctor — {url}  prefix={settings.OSTICKET_TABLE_PREFIX!r}\n")
    async with AsyncSessionLocal() as session:
        checks = await run_checks(session)
    for check in checks:
        print(f"[{_MARK[check.status]}] {check.name}" + (f" — {check.detail}" if check.detail else ""))
    failed = [c for c in checks if c.status == FAIL]
    warned = [c for c in checks if c.status == WARN]
    print(f"\n{len(checks) - len(failed) - len(warned)} ok, {len(warned)} warning(s), {len(failed)} failed")
    await engine.dispose()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
