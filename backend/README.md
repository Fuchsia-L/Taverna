# Backend (FastAPI)

## 技术栈

- Python FastAPI
- 单接口：`POST /api/chat`

## 目录结构

```text
backend/
  api/
  core/
  models/
  prompts/
```

## 启动方式

1. 创建并激活虚拟环境（可选）

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

2. 安装依赖

```bash
pip install -r requirements.txt
```

3. 启动服务

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

接口测试：

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"你好"}'
```

返回：

```json
{"reply":"你好，我是你的老师。这是一条测试回复。"}
```
