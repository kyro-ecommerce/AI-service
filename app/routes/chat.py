from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import process_chat_consultation

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat_consultation(request: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    """Smart AI Shopping Assistant Endpoint (RAG Chatbot).
    
    1. Retrieves relevant product context using RRF Hybrid Search.
    2. Generates natural tech purchasing advice via Gemini LLM or Intelligent Fallback.
    3. Returns response text and recommended product cards.
    """
    return await process_chat_consultation(
        message=request.message,
        limit=request.limit,
        db=db,
        user_id=request.user_id or 0,
    )


