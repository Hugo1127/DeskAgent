from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class JsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    # 防重放：每连接单调递增序号 + 客户端时间戳（epoch 秒），见 docs/COMMUNICATION_SECURITY.md
    seq: int | None = Field(default=None, strict=True)
    ts: int | None = Field(default=None, strict=True)


class EventPushEnvelope(BaseModel):
    kind: Literal["event"] = "event"
    event: dict[str, Any]  # Event.model_dump() 的序列化结果


class JsonRpcSuccess(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    result: Any


class JsonRpcErrorObject(BaseModel):
    code: int
    message: str
    data: Any = None


class JsonRpcError(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | None = None
    error: JsonRpcErrorObject


PARSE_ERROR = -32700      # 解析错误
INVALID_REQUEST = -32600  # 请求格式错误
METHOD_NOT_FOUND = -32601 # 方法不存在
INVALID_PARAMS = -32602   # 参数错误
INTERNAL_ERROR = -32603   # 服务器内部错误

# 应用层错误码（通信安全，见 docs/COMMUNICATION_SECURITY.md）
UNAUTHENTICATED = -32001  # 未认证或令牌错误
SESSION_FORBIDDEN = -32002 # 无权操作该 session
RATE_LIMITED = -32003     # 超出速率限制
AUTH_EXPIRED = -32004     # 令牌已失效（daemon 重启后旧连接）
STALE_SEQUENCE = -32005   # 报文序号重复/时间戳过期（防重放）


class HandlerError(Exception):
    """命令 handler 抛出此异常，SocketServer 将其转换为结构化 JSON-RPC 错误响应。"""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


# 构造一个 JSON-RPC 错误响应对象
def make_error(id: str | None, code: int, message: str, data: Any = None) -> JsonRpcError:
    return JsonRpcError(id=id, error=JsonRpcErrorObject(code=code, message=message, data=data))
