from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PlanBody(BaseModel):
    name: str = ""
    description: Optional[str] = None
    limits: List[Dict[str, Any]] = Field(default_factory=list)


class AssignBody(BaseModel):
    plan_id: Optional[str] = None


class SettingsBody(BaseModel):
    default_plan_id: Optional[str] = None
    exempt_admins: Optional[bool] = None
    day_timezone: Optional[str] = None
    contact_line: Optional[str] = None

    def provided(self) -> Dict[str, Any]:
        return {name: getattr(self, name) for name in self.model_fields_set}
