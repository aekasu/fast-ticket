"""Plain-text message bodies keep their line breaks.

osTicket stores a message as HTML, so a typed newline would collapse to a space. A body with no markup of its own
gets its newlines turned into ``<br>``; a body that already carries HTML is left as it is.
"""

import re
from typing import Annotated

from pydantic import BeforeValidator

_HAS_MARKUP = re.compile(r"</?[a-zA-Z][^>]*>")


def keep_line_breaks(value):
    if isinstance(value, str) and "\n" in value and not _HAS_MARKUP.search(value):
        return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    return value


MessageText = Annotated[str, BeforeValidator(keep_line_breaks)]
