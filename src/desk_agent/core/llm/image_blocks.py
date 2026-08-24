"""Anthropic 图片内容块（image content block）的构造与脱敏工具。

职责：把图片文件路径转换为 Anthropic Messages API 的 image content block
（base64 source），并提供脱敏占位、token 估算等下游安全处理。

未来扩展方向（设计预留）：
- 新增图片格式：在 SUPPORTED_IMAGE_MEDIA_TYPES 增加 扩展名 → media_type 映射即可
- 图片 token 精确估算：实现 estimate_image_tokens() 的按尺寸公式（tiles × 750 + 85），
  替代当前固定占位值 IMAGE_TOKEN_ESTIMATE
- 其他内容块类型（如 PDF document block）：在同级新建 pdf_blocks.py 等模块，
  复用本模块的校验/脱敏模式
- 图片来源变体（URL 等）：image_content_block() 增加 source 参数即可

本模块仅依赖标准库（base64 / pathlib）。
"""

from __future__ import annotations

import base64
from pathlib import Path

# 受支持的图片格式 → media_type（显式映射，不用 mimetypes，
# 因为 Windows 注册表映射不可靠且不完整）
SUPPORTED_IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Anthropic API 单张图片上限（官方文档：最大 5MB）
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# 估算 token 时图片块的固定占位值：图片 token 按尺寸计费，这里粗略取固定值，
# 避免把 base64 文本按字符数算成天量 token
IMAGE_TOKEN_ESTIMATE = 1_500


# 返回图片扩展名（小写带点），非图片返回 None
def image_suffix(path: str) -> str | None:
    p = Path(path).expanduser()
    suffix = p.suffix.lower()
    return suffix if suffix in SUPPORTED_IMAGE_MEDIA_TYPES else None


# 读取图片文件并构造 Anthropic image content block；
# 校验顺序：文件存在 → 扩展名受支持 → 大小 ≤ 5MB，失败抛中文异常（附修复建议）
def image_content_block(path: str) -> dict[str, object]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"图片文件不存在: {path}")

    suffix = p.suffix.lower()
    media_type = SUPPORTED_IMAGE_MEDIA_TYPES.get(suffix)
    if media_type is None:
        supported = "、".join(SUPPORTED_IMAGE_MEDIA_TYPES.values())
        raise ValueError(
            f"不支持的图片格式: {path}（{suffix or '无扩展名'}）。"
            f"支持格式: {supported}"
        )

    data = p.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        mb = len(data) / (1024 * 1024)
        raise ValueError(
            f"图片超过大小上限: {path}（{mb:.1f}MB > 5MB）。请压缩图片后重试。"
        )

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(data).decode("ascii"),
        },
    }


# 估算一个图片块的 token 数。当前返回固定占位值；
# 后续可按图片尺寸精确估算（Anthropic 公式：tiles × 750 + 85），在此扩展
def estimate_image_tokens(block: dict[str, object]) -> int:
    return IMAGE_TOKEN_ESTIMATE


# 图片块的脱敏占位描述（供 compactor / trace / 事件展示使用，不泄露 base64 数据）
def image_block_placeholder(block: dict[str, object]) -> str:
    source = block.get("source")
    media_type = ""
    size_bytes = 0
    if isinstance(source, dict):
        media_type = str(source.get("media_type", ""))
        data = source.get("data")
        if isinstance(data, str):
            # base64 长度 → 原始字节数（去掉填充的 3/4 换算）
            size_bytes = len(data) * 3 // 4
    if size_bytes >= 1024 * 1024:
        size = f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        size = f"{size_bytes // 1024}KB"
    return f"[image: {media_type or 'unknown'}, {size}]"
