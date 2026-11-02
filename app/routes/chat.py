from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.security import RequestIdentity, get_request_identity
from app.core.rate_limit import enforce_chat_rate_limit
from app.core.privacy import redact_pii
from app.schemas.chat import ChatFeedbackRequest, ChatFeedbackResponse, ChatRequest, ChatResponse
from app.repositories.user_interaction_repository import record_chat_feedback
from app.services.chat_service import process_chat_consultation, stream_chat_consultation

router = APIRouter()


@router.post("/chat/feedback", response_model=ChatFeedbackResponse)
async def chat_feedback(
    feedback_req: ChatFeedbackRequest,
    db: Session = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
    _rate_limit: None = Depends(enforce_chat_rate_limit),
) -> ChatFeedbackResponse:
    """Record explicit user feedback (thumbs_up / thumbs_down) on AI response."""
    record_chat_feedback(
        db=db,
        user_id=identity.resolve(feedback_req.user_id),
        message_text=redact_pii(feedback_req.message_text),
        feedback=feedback_req.feedback,
        session_id=feedback_req.session_id,
    )
    return ChatFeedbackResponse()



@router.post("/chat", response_model=ChatResponse)
async def chat_consultation(
    chat_req: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
    _rate_limit: None = Depends(enforce_chat_rate_limit),
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
        user_id=identity.resolve(chat_req.user_id),
        session_id=chat_req.session_id,
        http_client=http_client,
    )


@router.post("/chat/stream")
async def chat_consultation_stream(
    chat_req: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
    _rate_limit: None = Depends(enforce_chat_rate_limit),
) -> StreamingResponse:
    """Real-time SSE Streaming AI Shopping Assistant Endpoint.
    
    Streams response tokens chunks as text/event-stream.
    """
    http_client = getattr(request.app.state, "http_client", None)
    generator = stream_chat_consultation(
        message=chat_req.message,
        limit=chat_req.limit,
        db=db,
        user_id=identity.resolve(chat_req.user_id),
        session_id=chat_req.session_id,
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



