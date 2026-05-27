# PaperAgent

PaperAgent 是一个本地运行的多 Agent 科研论文管理与归纳助手。上传 PDF 后，系统自动调用 LLM 提取论文的题目、作者、摘要、主要贡献、方法/模型、实验结果、局限性、结论等结构化信息，支持搜索、筛选、标签管理和 AI 对话问答。

## 功能

- PDF 上传，LLM 自动提取结构化信息（标题、作者、摘要、贡献、方法、结果等）
- 论文管理：搜索、筛选（年份/领域/标签/收藏）、标签系统
- 语义搜索：基于向量检索的论文内容查找
- AI 对话：基于所有论文的结构化信息回答问题，支持跨论文对比和综述
- DashScope 百炼 Chat / Embedding API 集成；未配置 API Key 时使用本地降级
- React 工作台：论文库、论文详情、AI 对话
- Docker Compose 一键启动

## 目录

```text
backend/      FastAPI 后端
frontend/     React + Vite 前端
data/         本地运行数据、PDF、向量持久化
REQUIREMENTS.md
docker-compose.yml
.env.example
```

## 快速启动

```bash
cp .env.example .env
# 可选：在 .env 中填写 DASHSCOPE_API_KEY
docker compose up --build
```

访问：

- 前端：http://localhost:3000
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/api/health

## 本地开发

后端：

```bash
cd backend
docker compose -f docker-compose-dev.yml up -d
conda activate paperagent
pip install -r requirements.txt
$env:PYTHONPATH="."
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## 环境变量

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 空 | 阿里云百炼 API Key |
| `DASHSCOPE_CHAT_MODEL` | `qwen-plus` | 对话模型 |
| `DASHSCOPE_EMBEDDING_MODEL` | `text-embedding-v3` | 向量模型 |
| `DATABASE_URL` | `sqlite:///data/paperagent.db` | SQLite 路径 |
| `DATA_DIR` | `data` | 运行数据目录 |
| `PAPER_DIR` | `data/papers` | PDF 存储目录 |
| `VECTOR_DIR` | `data/chroma` | 本地向量存储目录 |
| `CHROMA_HOST` | 空 | Chroma server 地址；为空时使用嵌入式或本地降级 |
| `CHROMA_PORT` | `8001` | Chroma 端口 |

## API 摘要

- `POST /api/papers/upload` 上传 PDF，触发 LLM 结构化提取
- `GET /api/papers` 获取论文列表（支持筛选）
- `GET /api/papers/{id}` 获取论文结构化详情
- `GET /api/papers/{id}/download` 下载原始 PDF
- `PUT /api/papers/{id}` 编辑标签、收藏、领域
- `POST /api/papers/{id}/re-extract` 重新触发 LLM 提取
- `DELETE /api/papers/{id}` 删除论文
- `POST /api/search` 综合搜索（关键词+语义）
- `GET /api/search/semantic?q=...` 语义搜索
- `GET /api/tags` 获取用户所有标签
- `POST /api/tags` 创建标签
- `DELETE /api/tags/{id}` 删除标签
- `POST /api/qa/ask` 提问
- `GET /api/qa/history` 获取问答历史
- `POST /api/export` 导出论文为 Markdown

## 测试

```bash
cd backend
$env:PYTHONPATH="."
pytest
```
