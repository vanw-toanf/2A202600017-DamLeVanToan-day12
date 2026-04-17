# Deployment Information

## Public URL
https://2a202600017-damlevantoan-day12-06.up.railway.app/

## Platform
Railway

## Test Commands

### Health Check
```bash
curl https://2a202600017-damlevantoan-day12-06.up.railway.app/health
# Expected: {"status": "ok"}
```

### API Test (with authentication)
```bash
curl -X POST https://2a202600017-damlevantoan-day12-06.up.railway.app/ask \
  -H "X-API-Key: my-secret-key-1234" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
```

## Environment Variables Set
- `PORT` (Auto-injected by Railway)
- `REDIS_URL` (Sourced via Railway Reference)
- `AGENT_API_KEY`
- `LOG_LEVEL`

## Screenshots
- Mọi hình ảnh minh chứng hiện đang được lưu đầy đủ tại: `06-lab-complete/results_images/`
