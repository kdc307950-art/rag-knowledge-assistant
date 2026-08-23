# 数据结构定义：AIResponse 回答数据类
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

@dataclass
class AIResponse:
    content: str
    sources: Optional[List[str]] = None
    thought: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None