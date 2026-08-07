from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import process_chat_consultation, stream_chat_consultation

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat_consultation(
    chat_req: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ChatResponse:
    """Smart AI Shopping Assistant Endpoint (RAG Chatbot).
    
    1. Retrieves relevant product context using RRF Hybrid Search.
    2. Generates natural tech purchasing advice via Gemini LLM or Intelligent Fallback.
    3. Returns response text and recommended product cards.
    """
    http_client = getattr(request.app.state, "http_client", None)
    return await process_chat_consultation(
        message=chat_req.message,
        limit=chat_req.limit,
        db=db,
        user_id=chat_req.user_id or 0,
        http_client=http_client,
    )


@router.post("/chat/stream")
async def chat_consultation_stream(
    chat_req: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Real-time SSE Streaming AI Shopping Assistant Endpoint.
    
    Streams response tokens chunks as text/event-stream.
    """
    http_client = getattr(request.app.state, "http_client", None)
    generator = stream_chat_consultation(
        message=chat_req.message,
        limit=chat_req.limit,
        db=db,
        user_id=chat_req.user_id or 0,
        http_client=http_client,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )



