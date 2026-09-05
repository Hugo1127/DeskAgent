from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from desk_agent.core.llm.image_blocks import image_content_block
from desk_agent.core.tools.base import BaseTool, ToolResult
from desk_agent.core.tools.path_guard import require_relative_path


class ReadImageParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str


class ReadImageTool(BaseTool):
    params_model = ReadImageParams
    name = "read_image"
    description = (
        "Read an image file (png/jpg/jpeg/gif/webp, up to 5MB) and return its visual "
        "content so you can see it. Use for screenshots, diagrams, photos, "
        "or reading text from images. For text files use read_file instead."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path to the image file. Supports .png / .jpg / .jpeg / .gif / .webp, "
                    "up to 5MB."
                ),
            }
        },
        "required": ["path"],
    }

    # 读取图片并返回 [text 说明块, image 块]；仅允许相对路径，
    # 任何校验失败返回错误 ToolResult（内容恒为 str）
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        path_str = ReadImageParams.model_validate(params).path
        try:
            require_relative_path(path_str)
            image_block = image_content_block(path_str)
        except PermissionError as exc:
            return ToolResult(
                content=f"read_image 失败: {exc}",
                is_error=True,
                error_type="permission_denied",
            )
        except (FileNotFoundError, ValueError, OSError) as exc:
            return ToolResult(
                content=f"read_image 失败: {exc}",
                is_error=True,
                error_type="runtime_error",
            )

        return ToolResult(
            content=[
                {"type": "text", "text": f"[read_image] {path_str}"},
                image_block,
            ]
        )
