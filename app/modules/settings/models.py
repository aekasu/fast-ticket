from pydantic import BaseModel, Field


class AttachmentLimits(BaseModel):
    """What an upload may be, so a client can refuse a file before sending it."""

    max_file_bytes: int = Field(description="Largest file one request may carry: the smaller of osTicket's and the API's caps")
    osticket_max_file_bytes: int = Field(description="osTicket's own maximum file size (Admin Panel > Settings > Tickets)")
    api_max_file_bytes: int = Field(description="The API's cap per file")
    max_files: int = Field(description="Most files in one request")
    max_total_bytes: int = Field(description="Most bytes of files in one request")
