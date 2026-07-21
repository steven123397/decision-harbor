"""请求/响应模式。"""
from pydantic import BaseModel, Field


class QueryRunCreate(BaseModel):
    sql: str = Field(..., min_length=1, description="用户提交的显式 SQL")


class ColumnDef(BaseModel):
    name: str
    type: str
