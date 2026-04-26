from pydantic import BaseModel, Field


class ProspectRequest(BaseModel):
    icp: str = Field(..., min_length=3)
    sender_info: str = Field(..., min_length=3)
    goal: str = Field(default="Book a 15-minute discovery call")
    max_leads: int = Field(default=1, ge=1, le=10)
