"""API 错误类型。

只有 ParamError（请求参数不合法）会被全局映射成 400 + 原始提示；
其余 ValueError/异常仍走 500 并由 uvicorn 记录 traceback，避免内部错误被伪装成参数错误。
"""
from __future__ import annotations


class ParamError(ValueError):
    """请求参数不合法（项目编号、分段编号、切点等）。"""

