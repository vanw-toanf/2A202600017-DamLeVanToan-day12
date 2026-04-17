"""
Production AI Agent — Kết hợp tất cả Day 12 concepts

Checklist:
  ✅ Config từ environment (12-factor)
  ✅ Structured JSON logging
  ✅ API Key authentication
  ✅ Rate limiting
  ✅ Cost guard
  ✅ Input validation (Pydantic)
  ✅ Health check + Readiness probe
  ✅ Graceful shutdown
  ✅ Security headers
  ✅ CORS
  ✅ Error handling
"""
import os
import time
import signal
import logging
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Security, Depends, Request, Response
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import redis

from app.config import settings

# Mock LLM (thay bằng OpenAI/Anthropic khi có API key)
from utils.mock_llm import ask as llm_ask

redis_client = redis.Redis.from_url(settings.redis_url or "redis://localhost:6379", decode_responses=True)

# ─────────────────────────────────────────────────────────
# Logging — JSON structured
# ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
_is_ready = False
_request_count = 0
_error_count = 0

# ─────────────────────────────────────────────────────────
# Redis Rate Limiter (Sliding Window)
# ─────────────────────────────────────────────────────────
def check_rate_limit(key: str):
    now = time.time()
    try:
        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(f"rate:{key}", 0, now - 60)
        pipe.zcard(f"rate:{key}")
        pipe.zadd(f"rate:{key}", {str(now): now})
        pipe.expire(f"rate:{key}", 60)
        results = pipe.execute()
        
        count = results[1]
        if count >= settings.rate_limit_per_minute:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {settings.rate_limit_per_minute} req/min",
                headers={"Retry-After": "60"},
            )
    except redis.RedisError as e:
        logger.error(f"Redis error: {e}")

# ─────────────────────────────────────────────────────────
# Redis Cost Guard
# ─────────────────────────────────────────────────────────
def check_and_record_cost(key: str, input_tokens: int, output_tokens: int):
    month = time.strftime("%Y-%m")
    cost_key = f"budget:{key}:{month}"
    
    try:
        current_cost = float(redis_client.get(cost_key) or 0.0)
        if current_cost >= settings.monthly_budget_usd:
            raise HTTPException(402, "Monthly budget exhausted.")
        
        cost = (input_tokens / 1000) * 0.00015 + (output_tokens / 1000) * 0.0006
        if cost > 0:
            redis_client.incrbyfloat(cost_key, cost)
            redis_client.expire(cost_key, 31*24*3600)
    except redis.RedisError as e:
        logger.error(f"Redis error: {e}")

# ─────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    if not api_key or api_key != settings.agent_api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Include header: X-API-Key: <key>",
        )
    return api_key

# ─────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_ready
    logger.info(json.dumps({
        "event": "startup",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }))
    try:
        redis_client.ping()
        _is_ready = True
        logger.info(json.dumps({"event": "ready", "redis": "connected"}))
    except redis.RedisError as e:
        logger.error(json.dumps({"event": "error", "message": f"Redis connection failed: {e}"}))

    yield

    _is_ready = False
    logger.info(json.dumps({"event": "shutdown"}))

# ─────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

@app.middleware("http")
async def request_middleware(request: Request, call_next):
    global _request_count, _error_count
    start = time.time()
    _request_count += 1
    try:
        response: Response = await call_next(request)
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        try:
            del response.headers["server"]
        except KeyError:
            pass
        duration = round((time.time() - start) * 1000, 1)
        logger.info(json.dumps({
            "event": "request",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": duration,
        }))
        return response
    except Exception as e:
        _error_count += 1
        raise

# ─────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000,
                          description="Your question for the agent")

class AskResponse(BaseModel):
    question: str
    answer: str
    model: str
    timestamp: str

# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────

@app.get("/", tags=["Info"])
def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "endpoints": {
            "ask": "POST /ask (requires X-API-Key)",
            "health": "GET /health",
            "ready": "GET /ready",
        },
    }


@app.post("/ask", response_model=AskResponse, tags=["Agent"])
async def ask_agent(
    body: AskRequest,
    request: Request,
    _key: str = Depends(verify_api_key),
):
    """
    Send a question to the AI agent.

    **Authentication:** Include header `X-API-Key: <your-key>`
    """
    user_id = _key[:8]
    # Rate limit per API key
    check_rate_limit(user_id)

    # Budget check
    input_tokens = len(body.question.split()) * 2
    check_and_record_cost(user_id, input_tokens, 0)

    logger.info(json.dumps({
        "event": "agent_call",
        "q_len": len(body.question),
        "client": str(request.client.host) if request.client else "unknown",
    }))

    # Get conversation history from Redis
    history_key = f"history:{user_id}"
    try:
        history = redis_client.lrange(history_key, 0, -1)
        history_context = "\n".join(history[-5:]) if history else ""
    except redis.RedisError:
        history_context = ""

    # Call LLM (mock)
    answer = llm_ask(body.question)

    # Save to Redis
    try:
        redis_client.rpush(history_key, f"User: {body.question}", f"Agent: {answer}")
        redis_client.expire(history_key, 24 * 3600)
    except redis.RedisError as e:
        logger.error(f"Redis error saving history: {e}")

    output_tokens = len(answer.split()) * 2
    check_and_record_cost(user_id, 0, output_tokens)

    return AskResponse(
        question=body.question,
        answer=answer,
        model=settings.llm_model,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/health", tags=["Operations"])
def health():
    """Liveness probe. Platform restarts container if this fails."""
    status = "ok"
    checks = {"llm": "mock" if not settings.openai_api_key else "openai"}
    return {
        "status": status,
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready", tags=["Operations"])
def ready():
    """Readiness probe. Load balancer stops routing here if not ready."""
    if not _is_ready:
        raise HTTPException(503, "Not ready")
    return {"ready": True}


@app.get("/metrics", tags=["Operations"])
def metrics(_key: str = Depends(verify_api_key)):
    """Basic metrics (protected)."""
    user_id = _key[:8]
    month = time.strftime("%Y-%m")
    try:
        current_cost = float(redis_client.get(f"budget:{user_id}:{month}") or 0.0)
    except redis.RedisError:
        current_cost = 0.0
    
    return {
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "error_count": _error_count,
        "monthly_cost_usd": round(current_cost, 4),
        "monthly_budget_usd": settings.monthly_budget_usd,
        "budget_used_pct": round(current_cost / settings.monthly_budget_usd * 100, 1),
    }


# ─────────────────────────────────────────────────────────
# Graceful Shutdown
# ─────────────────────────────────────────────────────────
def _handle_signal(signum, _frame):
    logger.info(json.dumps({"event": "signal", "signum": signum}))

signal.signal(signal.SIGTERM, _handle_signal)


if __name__ == "__main__":
    logger.info(f"Starting {settings.app_name} on {settings.host}:{settings.port}")
    logger.info(f"API Key: {settings.agent_api_key[:4]}****")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        timeout_graceful_shutdown=30,
    )
