"""裁判書資料模型"""

from pydantic import BaseModel, Field
from datetime import datetime


class JudgmentSearchItem(BaseModel):
    """搜尋結果中的單一裁判書摘要"""
    case_id: str = Field(description="案號")
    court: str = Field(default="", description="法院名稱")
    case_type: str = Field(default="", description="民事/刑事/行政")
    date: str = Field(default="", description="裁判日期")
    cause: str = Field(default="", description="案由")
    summary: str = Field(default="", description="摘要")
    url: str = Field(default="", description="全文連結")
    jid: str = Field(default="", description="JID 識別碼")


class JudgmentDocument(BaseModel):
    """完整裁判書"""
    case_id: str = Field(description="案號")
    court: str = Field(default="", description="法院名稱")
    date: str = Field(default="", description="裁判日期")
    judges: list[str] = Field(default_factory=list, description="法官")
    parties: dict = Field(default_factory=dict, description="當事人")
    cause: str = Field(default="", description="案由")
    main_text: str = Field(default="", description="主文")
    facts: str = Field(default="", description="事實")
    reasoning: str = Field(default="", description="理由")
    cited_statutes: list[str] = Field(default_factory=list, description="引用法條")
    cited_cases: list[str] = Field(default_factory=list, description="引用判決")
    full_text: str = Field(default="", description="完整原文")
    source_url: str = Field(default="", description="來源 URL")
