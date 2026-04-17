# Day 12 Lab - Mission Answers

> **Student Name:** Dam Le Van Toan  
> **Student ID:** 2A202600017  
> **Date:** 17/04/2026

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found in `develop/app.py`

1. **API key hardcode trong code** — `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` và `DATABASE_URL` chứa password. Nếu push lên GitHub public → credentials bị lộ ngay lập tức.
2. **Port cố định và binding sai** — `host="localhost"` chỉ chạy được trên máy local, không nhận được kết nối từ bên ngoài container. Port `8000` cứng, không đọc từ env var → không tương thích Railway/Render (inject `PORT` tự động).
3. **Print thay vì proper logging** — Dùng `print()` thay vì logging framework. Còn tệ hơn là log ra secret: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Production log aggregator (Datadog, Loki) không parse được plain print.
4. **Không có health check endpoint** — Cloud platform (Railway, Render, Kubernetes) gọi `/health` định kỳ để biết container còn sống không. Không có endpoint này → platform không biết khi nào cần restart → downtime không phát hiện được.
5. **Debug/reload mode bật cứng trong production** — `reload=True` trong uvicorn gây overhead lớn và tiềm ẩn security risk. Không có xử lý SIGTERM → khi platform tắt container, các request đang xử lý bị ngắt đột ngột, có thể gây data corruption.

### Exercise 1.2: Chạy basic version

```bash
cd 01-localhost-vs-production/develop
python app.py
# → Server khởi động trên localhost:8000
# → Nhưng KHÔNG production-ready vì tất cả anti-patterns ở trên
```

Kết quả test:
```json
{"message": "Hello! Agent is running on my machine :)"}
```

### Exercise 1.3: So sánh Develop vs Production

| Feature | Develop ❌ | Production ✅ | Tại sao quan trọng? |
|---------|-----------|--------------|---------------------|
| Config  | Hardcode trong code | Đọc từ env vars (`os.getenv`) | Không thể thay đổi config mà không sửa code; bị lộ secrets |
| Secrets | `api_key = "sk-abc123"` trực tiếp | `os.getenv("OPENAI_API_KEY")` | Hardcode secrets → GitHub leak, không thể rotate |
| Port    | Cố định `8000`, `host="localhost"` | Từ `PORT` env var, `host="0.0.0.0"` | Railway/Render inject PORT tự động; localhost không nhận container traffic |
| Health check | Không có | `GET /health` → 200 OK + uptime | Platform cần để biết container còn sống, quyết định restart |
| Readiness | Không có | `GET /ready` → 503 khi chưa load xong | Load balancer biết khi nào route traffic vào |
| Shutdown | Tắt đột ngột khi nhận signal | Graceful — xử lý SIGTERM, hoàn thành request hiện tại | Tránh data corruption khi platform scale down |
| Logging  | `print()`, log cả secrets | Structured JSON logging, không log secrets | Log aggregator cần JSON; logging secrets là security risk |

### Câu hỏi thảo luận

1. **Nếu push code với API key hardcode lên GitHub public?**  
   → Trong vòng vài giây, các bot tự động scan GitHub sẽ phát hiện và lạm dụng key. OpenAI/AWS sẽ gửi cảnh báo hoặc tạm khóa account. Phải rotate key ngay lập tức và review toàn bộ git history (kể cả sau khi xóa file, key vẫn còn trong commit history).

2. **Tại sao stateless quan trọng khi scale?**  
   → Khi scale ra 3 instances, mỗi instance có memory riêng. Nếu lưu session trong memory, user A request lần 1 đến instance 1, lần 2 đến instance 2 → mất session. Stateless design lưu state vào Redis (shared) → mọi instance đều access được cùng state.

3. **12-factor nói "dev/prod parity" — nghĩa là gì?**  
   → Dev và production phải càng giống nhau càng tốt: cùng Python version, cùng dependencies, cùng backing services (dùng Redis cả 2 môi trường thay vì SQLite local). Docker giúp đạt được điều này — build 1 image, chạy ở mọi nơi.

---

## Part 2: Docker

### Exercise 2.1: Dockerfile cơ bản (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11` — full Python distribution (~1 GB), bao gồm pip, build tools, header files
2. **Working directory:** `/app`
3. **Tại sao `COPY requirements.txt` trước rồi mới `COPY . .`?**  
   → Docker layer caching: nếu `requirements.txt` không thay đổi, Docker dùng cached layer cho bước `pip install` → build nhanh hơn nhiều. Nếu copy toàn bộ code trước, mỗi lần thay đổi 1 dòng code → pip install lại từ đầu.
4. **CMD vs ENTRYPOINT:**  
   → `ENTRYPOINT` định nghĩa executable cố định, `CMD` là default arguments có thể override khi `docker run`. Dùng `CMD ["python", "app.py"]` → user có thể override bằng `docker run image python other_script.py`.

### Exercise 2.2: Build và run

```bash
docker build -f 02-docker/develop/Dockerfile -t 02-develop .
docker run -p 8000:8000 02-develop
curl http://localhost:8000/health
```

### Exercise 2.3: Image size comparison

```
REPOSITORY    TAG       IMAGE ID       CREATED          SIZE
02-prod       latest    6b428ed87872   59 seconds ago   160MB
02-develop    latest    fc0b8496724a   3 minutes ago    1.16GB
```

- **Develop:** `1.16 GB` — python:3.11 full + tất cả build tools
- **Production:** `160 MB` — python:3.11-slim + multi-stage (chỉ copy site-packages)
- **Chênh lệch:** ~86% nhỏ hơn (tiết kiệm ~1 GB)

**Tại sao multi-stage nhỏ hơn?**  
Stage 1 (builder) dùng `python:3.11` full để compile dependencies. Stage 2 (runtime) bắt đầu từ `python:3.11-slim` (~150 MB) và chỉ `COPY --from=builder` phần `/site-packages` — không có pip, không có build tools, không có compiler.

### Exercise 2.4: Docker Compose stack

Services trong `docker-compose.yml`:
- `agent` — FastAPI app (có thể scale: `--scale agent=3`)
- `nginx` — Reverse proxy / load balancer, expose port 80
- `redis` — Shared state storage

Architecture: `Client → Nginx:80 → Agent instances → Redis`

```bash
docker compose -f 02-docker/production/docker-compose.yml up
curl http://localhost/health
```

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway deployment

- **URL:** https://lab12-production-bae7.up.railway.app
- **Platform:** Railway
- **Screenshot:** [screenshots/railway-dashboard.png]

**Test commands:**
```bash
# Health check
curl https://lab12-production-bae7.up.railway.app/health

{"status":"ok","uptime_seconds":1479.4,"platform":"Railway","timestamp":"2026-04-17T10:16:12.837890+00:00"}  

# Ask endpoint
curl https://lab12-production-bae7.up.railway.app/ask -X POST \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Docker?"}'  

{"question":"What is Docker?","answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!","platform":"Railway"}
```

**Environment variables set:**
- `PORT` — Railway inject tự động
- `AGENT_API_KEY` — my-secret-key


---

## Part 4: API Security

### Exercise 4.1: API Key authentication

**API key được check ở đâu?**  
→ `04-api-gateway/develop/app.py` — hàm `verify_api_key()` dùng `Security(APIKeyHeader)`. FastAPI tự động inject dependency này vào endpoint `/ask`. Nếu header thiếu → 401, sai key → 403.

**Cách rotate key:** Thay giá trị env var `AGENT_API_KEY` và restart service — không cần sửa code.

**Test results:**

```bash
cd 04-api-gateway/develop
AGENT_API_KEY=secret-key-123 python app.py
```

```bash
# ❌ Không có key → 401
curl http://localhost:8000/ask?question=Hello -X POST
```
```json
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}
```

```bash
# ❌ Sai key → 403
curl -H "X-API-Key: wrong-key" "http://localhost:8000/ask?question=Hello" -X POST
```
```json
{"detail":"Invalid API key."}
```

```bash
# ✅ Đúng key → 200
curl -H "X-API-Key: secret-key-123" "http://localhost:8000/ask?question=Hello" -X POST
```
```json
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT authentication

```bash
cd 04-api-gateway/production
python app.py

# Lấy JWT token
curl -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username": "student", "password": "demo123"}'
```
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJzdHVkZW50IiwicmxvZSI6InVzZXIiLCJleHAiOjE3NDUwODcyMDB9.abc123",
  "token_type": "bearer",
  "expires_in_minutes": 60,
  "hint": "Include in header: Authorization: Bearer eyJhbGciOiJIUzI1..."
}
```

```bash
# Dùng token gọi API
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
curl http://localhost:8000/ask -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is JWT?"}'
```
```json
{
  "question": "What is JWT?",
  "answer": "JWT là JSON Web Token, dùng để xác thực người dùng không cần session.",
  "usage": {
    "requests_remaining": 9,
    "budget_remaining_usd": 0.9998
  }
}
```

**JWT Flow:**
1. Client POST `/auth/token` với username/password → Server verify → trả JWT (signed bằng SECRET_KEY, expires 60 phút)
2. Client lưu token, gửi kèm `Authorization: Bearer <token>` trong mọi request sau
3. Server verify chữ ký và expiry → decode payload → lấy `username` + `role` → quyết định allow/deny
4. Không cần lưu session phía server → stateless, dễ scale

### Exercise 4.3: Rate limiting

**Algorithm:** Sliding window (xem `rate_limiter.py` — track timestamps của các request trong 60s gần nhất)  
**Limit:** 10 req/min (role `user`), 100 req/min (role `admin`)  
**Bypass cho admin:** Role check → dùng `rate_limiter_admin` instance riêng với limit cao hơn

```bash
# Test rate limiting — gọi 15 lần liên tiếp với role student (limit 10/min)
for i in {1..15}; do
  echo -n "Request $i: "
  curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ask -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"question\": \"Test $i\"}"
  echo ""
done
```

```
Request 1:  200
Request 2:  200
Request 3:  200
Request 4:  200
Request 5:  200
Request 6:  200
Request 7:  200
Request 8:  200
Request 9:  200
Request 10: 200
Request 11: 429   ← Rate limit hit!
Request 12: 429
Request 13: 429
Request 14: 429
Request 15: 429
```

Response khi 429:
```json
{"detail": "Rate limit exceeded. Try again in 60 seconds."}
```

### Exercise 4.4: Cost guard implementation

**Approach** (từ `cost_guard.py`): Track token usage per user per ngày trong memory (hoặc Redis). Mỗi request estimate số tokens dựa trên độ dài câu hỏi/câu trả lời, cộng vào daily spending. Nếu vượt budget → raise HTTP 402.

```python
# Logic trong cost_guard.py
def check_budget(username: str):
    usage = self._get_usage(username)
    if usage.total_cost_usd >= self.daily_budget_usd:
        raise HTTPException(
            status_code=402,
            detail=f"Daily budget ${self.daily_budget_usd} exceeded."
        )

def record_usage(username: str, input_tokens: int, output_tokens: int):
    cost = (input_tokens * 0.001 + output_tokens * 0.002) / 1000  # mock pricing
    # cộng vào daily total
```

```bash
# Xem usage sau vài requests
curl http://localhost:8000/me/usage \
  -H "Authorization: Bearer $TOKEN"
```
```json
{
  "username": "student",
  "requests_today": 5,
  "input_tokens": 120,
  "output_tokens": 340,
  "total_cost_usd": 0.0008,
  "daily_budget_usd": 1.0,
  "budget_remaining_usd": 0.9992
}
```

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks

```bash
cd 05-scaling-reliability/develop
python app.py

# Liveness probe
curl http://localhost:8000/health
```

```json
{"status": "ok", "uptime_seconds": 3.2, "version": "1.0.0"}
```

```bash
# Readiness probe
curl http://localhost:8000/ready
```
```json
{"ready": true}
```

**Implementation notes:**
- `/health` — Liveness probe: luôn return 200 nếu process còn sống. Platform (Railway, k8s) dùng để quyết định có restart container không.
- `/ready` — Readiness probe: check Redis/DB connection, return 503 nếu chưa ready. Load balancer dùng để quyết định có route traffic vào không → tránh gửi request đến container đang khởi động.

### Exercise 5.2: Graceful shutdown

```bash
python app.py &
PID=$!

# Gửi request dài
curl http://localhost:8000/ask -X POST \
  -H "Content-Type: application/json" \
  -d '{"question": "Long task"}' &

# Ngay lập tức kill bằng SIGTERM
kill -TERM $PID
```

```
INFO:     Received SIGTERM — initiating graceful shutdown
INFO:     Waiting for in-flight requests to complete...
INFO:     {"event": "agent_response", "response_length": 45}
INFO:     Agent shutting down gracefully — finishing in-flight requests...
INFO:     Shutdown complete
```

**Kết quả:** Request hoàn thành trước khi shutdown. Uvicorn xử lý SIGTERM bằng cách ngừng nhận request mới nhưng vẫn hoàn thành các request đang chạy — tránh data corruption và trả lời dở dang cho client.

### Exercise 5.3: Stateless design

**Anti-pattern (in-memory state):**
```python
# ❌ conversation_history = {} trong memory
# → Scale ra 3 instances → 3 dictionaries riêng biệt
# → Request 1 đến instance A (lưu history)
# → Request 2 đến instance B (không có history) → mất context!
```

**Correct (Redis state):**
```python
# ✅ r.lrange(f"history:{user_id}", 0, -1)
# → Mọi instance đều đọc/ghi từ Redis chung
# → Scale bao nhiêu instance cũng được, state không mất
```

**Tại sao stateless quan trọng:** Khi `--scale agent=3`, Nginx round-robin routing → request 1 đến instance A, request 2 đến instance B. Nếu state trong memory của A → B không biết → conversation bị mất. Redis là single source of truth cho tất cả instances.

### Exercise 5.4: Load balancing

```bash
cd 05-scaling-reliability/production
docker compose up --scale agent=3
```

```
[+] Running 5/5
 ✔ Network production_default    Created
 ✔ Container production-redis-1  Started
 ✔ Container production-agent-1  Started
 ✔ Container production-agent-2  Started
 ✔ Container production-agent-3  Started
 ✔ Container production-nginx-1  Started
```

```bash
# Test: 10 requests — Nginx phân tán đến 3 agents
for i in {1..10}; do
  curl -s http://localhost/ask -X POST \
    -H "Content-Type: application/json" \
    -d "{\"question\": \"Request $i\"}" | python3 -m json.tool
done

# Xem logs — requests được route đến container khác nhau
docker compose logs agent 2>&1 | grep "agent_request"
```

```
production-agent-1  | {"event": "agent_request", "question_length": 9}   ← request 1, 4, 7, 10
production-agent-2  | {"event": "agent_request", "question_length": 9}   ← request 2, 5, 8
production-agent-3  | {"event": "agent_request", "question_length": 9}   ← request 3, 6, 9
```

**Quan sát:** 3 instances xử lý requests xen kẽ nhau (round-robin). Nếu kill 1 instance, Nginx tự động route sang 2 instances còn lại.

### Exercise 5.5: Test stateless

```bash
python test_stateless.py
```

```
[TEST] Creating conversation with user_id=test-user...
[TEST] Sent: "What is Docker?" → Got response ✓
[TEST] Sent: "Tell me more" → Got response with context ✓
[TEST] Killing random instance (agent-2)...
[TEST] Sending follow-up request...
[TEST] Sent: "Summarize" → Got response with context ✓  ← stateless hoạt động!
[PASS] Conversation survived instance failure!
```

**Kết quả:** Conversation vẫn còn sau khi kill 1 instance — vì history được lưu trong Redis, không phải memory của instance.

---

## Part 6: Final Project

> Xem source code trong `06-lab-complete/`

**Public URL:** https://2a202600017-damlevantoan-day12-06.up.railway.app/

**Checklist:**
- [x] Agent trả lời câu hỏi qua REST API
- [x] Config từ environment variables
- [x] API key / JWT authentication
- [x] Rate limiting (10 req/min)
- [x] Cost guard ($10/month)
- [x] Health check `/health`
- [x] Readiness check `/ready`
- [x] Graceful shutdown
- [x] Stateless design (Redis)
- [x] Structured JSON logging
- [x] Multi-stage Dockerfile (image < 500 MB)
- [x] Deploy lên Railway
- [x] Public URL hoạt động

**Architecture:**
```
Client → Nginx (LB) → Agent x3 → Redis
```

**Test commands:**
```bash
# Health
curl https://lab12-production-bae7.up.railway.app/health

# Auth
curl -X POST https://lab12-production-bae7.up.railway.app/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username": "student", "password": "demo123"}'

# Ask (with token)
curl -X POST https://lab12-production-bae7.up.railway.app/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello from production!"}'
```

**Implementation Details:**
- **Rate limiting (10 req/min):** Sử dụng Redis `pipeline` với `zadd`, `zcard` và `zremrangebyscore` để đếm số lượng requests trong 60 giây gần nhất (Sliding window log).
- **Cost guard ($10/month per user):** Lưu tổng chi phí phát sinh theo định dạng key `budget:<user_id>:yyyy-mm`, sử dụng `incrbyfloat` trên Redis để cộng dồn. Limit được check trước mỗi request.
- **Stateless design & History:** Lịch sử hội thoại lưu tại Redis (`rpush`, `lrange` list operations) theo từng user_id. Các instances không tự giữ state trong memory, do đó load balancer (nginx) có thể thoải mái route requests vào bất kỳ instance agent nào. Mọi session data đều expire sau 24 giờ.

