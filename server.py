"""
北京文旅文投 - 后端代理服务
将扣子智能体 API 调用放在服务端，保护 API Token 不暴露在前端
使用流式方式 (stream=True) 获取完整回答，无需额外调用 retrieve/message API
"""

import os
import re
import json
import logging
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
import httpx

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("server")

# ==================== 配置 ====================
COZE_API_TOKEN = os.environ.get("COZE_API_TOKEN")
if not COZE_API_TOKEN:
    raise RuntimeError("COZE_API_TOKEN 未设置，请配置环境变量")
COZE_BOT_ID = os.environ.get("COZE_BOT_ID", "7644841800452735003")
COZE_API_BASE = os.environ.get("COZE_API_BASE", "https://api.coze.cn")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

if not COZE_API_TOKEN:
    logger.warning("⚠️ COZE_API_TOKEN 未配置！")


# ==================== 数据模型 ====================
class Message(BaseModel):
    role: str = Field(..., description="消息角色: user 或 assistant")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    messages: List[Message] = Field(..., description="对话历史消息列表")
    user_id: Optional[str] = Field(default="travel_user", description="用户标识")


class ChatResponse(BaseModel):
    content: str = Field(..., description="AI 回答内容")


def parse_sse_events(text: str):
    """
    解析 Coze API 返回的 SSE 事件流。
    返回事件列表 [(event_type, data_dict), ...]
    """
    events = []
    # 按 event: 分割（保留第一空部分）
    parts = re.split(r"\nevent:", text)
    for part in parts:
        part = part.strip()
        if not part:
            continue

        lines = part.split("\n")
        event_type = lines[0].strip()  # 第一行是 event 类型

        # 找所有 data: 行的内容并合并
        data_parts = []
        for line in lines[1:]:
            if line.startswith("data:"):
                data_parts.append(line[5:].strip())

        if data_parts:
            data_str = "".join(data_parts)
            try:
                data = json.loads(data_str)
                events.append((event_type, data))
            except json.JSONDecodeError:
                events.append((event_type, {"raw": data_str}))

    return events


async def call_coze_bot(messages: list, user_id: str) -> str:
    """
    通过流式 SSE 调用扣子智能体，返回完整回答内容。
    只需要 v3/chat 一个 API 即可完成。
    """
    additional_messages = [
        {"role": msg["role"], "content": msg["content"], "content_type": "text"}
        for msg in messages
    ]

    headers = {
        "Authorization": f"Bearer {COZE_API_TOKEN}",
        "Content-Type": "application/json",
    }

    request_body = {
        "bot_id": COZE_BOT_ID,
        "user_id": user_id,
        "stream": True,
        "additional_messages": additional_messages,
        "auto_save_history": True,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        logger.info(f"📤 发送请求 | messages: {len(additional_messages)}条 | user: {user_id}")

        async with client.stream(
            "POST", f"{COZE_API_BASE}/v3/chat", headers=headers, json=request_body,
        ) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                logger.error(f"❌ API 请求失败 ({response.status_code}): {error_text[:200]}")
                raise HTTPException(
                    status_code=502,
                    detail=f"扣子API调用失败: HTTP {response.status_code}",
                )

            # 完整接收所有数据
            all_text = ""
            async for chunk in response.aiter_text():
                all_text += chunk

            # 解析 SSE 事件
            events = parse_sse_events(all_text)
            logger.info(f"📥 收到 {len(events)} 个事件")

            answer_content = ""
            for evt_type, data in events:
                if evt_type == "conversation.message.completed":
                    if data.get("type") == "answer":
                        content = data.get("content", "")
                        if content:
                            answer_content = content
                elif evt_type == "error":
                    error_msg = data.get("msg", "未知错误")
                    logger.error(f"❌ SSE 错误: {error_msg}")
                    raise HTTPException(status_code=502, detail=f"智能体错误: {error_msg}")

            if not answer_content:
                logger.error("❌ 未获取到 AI 回答内容")
                # 打印所有事件类型用于调试
                for evt_type, data in events:
                    logger.info(f"  事件: {evt_type} -> {json.dumps(data, ensure_ascii=False)[:100]}")
                raise HTTPException(status_code=502, detail="未获取到 AI 回答")

            logger.info(f"✅ 回答完成 | 长度: {len(answer_content)}字符")
            return answer_content


# ==================== FastAPI 应用 ====================
app = FastAPI(
    title="北京文旅文投 - 后端代理服务",
    description="为北京文旅AI平台提供扣子智能体API代理，保护API认证安全",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", summary="健康检查")
async def health_check():
    return {
        "status": "ok",
        "bot_id": COZE_BOT_ID,
        "token_configured": bool(COZE_API_TOKEN),
    }


@app.post("/api/chat", response_model=ChatResponse, summary="与扣子智能体对话")
async def chat_with_bot(request: ChatRequest):
    if not COZE_API_TOKEN:
        raise HTTPException(status_code=500, detail="服务端未配置 COZE_API_TOKEN")

    content = await call_coze_bot(
        messages=[msg.model_dump() for msg in request.messages],
        user_id=request.user_id or "travel_user",
    )
    return ChatResponse(content=content)


# ==================== 托管前端静态页面 ====================
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
static_file = os.path.join(STATIC_DIR, "beijing_travel.html")
if os.path.exists(static_file):
    from fastapi.responses import HTMLResponse

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_frontend():
        """托管前端页面 - 访问 http://localhost:8000 即可打开"""
        with open(static_file, "r", encoding="utf-8") as f:
            return f.read()

    logger.info(f"🌐 前端页面已托管: {static_file}")
else:
    logger.warning("⚠️ beijing_travel.html 未找到")


# ==================== 启动入口 ====================
if __name__ == "__main__":
    logger.info(f"🚀 启动服务 | 端口: {SERVER_PORT}")
    logger.info(f"🤖 Bot ID: {COZE_BOT_ID}")
    logger.info(f"🔑 Token: {'已配置 ✅' if COZE_API_TOKEN else '未配置 ❌'}")
    logger.info(f"🌐 API Base: {COZE_API_BASE}")
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)
