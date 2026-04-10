"""法規資料模型"""

from pydantic import BaseModel, Field


class LawInfo(BaseModel):
    """法規基本資訊"""
    pcode: str = Field(description="法規代碼")
    name: str = Field(description="法規名稱")
    category: str = Field(default="", description="法律/命令")
    last_amended: str = Field(default="", description="最後修正日期")
    status: str = Field(default="", description="法規狀態（現行法規/已廢止）")


class ArticleContent(BaseModel):
    """單一條文"""
    number: str = Field(description="條號")
    content: str = Field(description="條文全文")
    history: list[dict] = Field(default_factory=list, description="修法沿革")
    english: str = Field(default="", description="英譯")


class RegulationSearchItem(BaseModel):
    """法規搜尋結果項目"""
    pcode: str = Field(description="法規代碼")
    name: str = Field(description="法規名稱")
    category: str = Field(default="", description="法規分類")
    status: str = Field(default="", description="法規狀態（現行法規/已廢止）")
    last_amended: str = Field(default="", description="最後修正日期")
