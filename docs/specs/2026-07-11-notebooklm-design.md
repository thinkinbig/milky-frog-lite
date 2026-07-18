# NotebookLM 系统设计文档

## 概述

本文档定义了一个类 NotebookLM 系统的微服务架构设计方案。系统支持文档导入、RAG 问答、音频播客生成、笔记标注和文档源管理。

## 架构概览

```
┌──────────┐      ┌─────────────────┐
│  Client  │─────▶│  API Gateway    │
│ (Web/App)│◀────│(Auth, RateLimit)│
└──────────┘      └───┬─┬─┬─┬─┬────┘
                      │ │ │ │ │
              ┌───────┘ │ │ │ └───────┐
              ▼         ▼ ▼ ▼         ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │Ingestion │ │RAG       │ │Audio     │
        │Service   │ │Service   │ │Service   │
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │            │            │
             ▼            ▼            ▼
        ┌─────────────────────────────────────┐
        │         Shared Infrastructure       │
        │  (Postgres, Vector DB, Object Store,│
        │   Message Queue, TTS Engine)        │
        └─────────────────────────────────────┘

        ┌──────────┐      ┌──────────┐
        │Source    │      │Note      │
        │Service   │      │Service   │
        └──────────┘      └──────────┘
```

## 技术栈假设

- **消息队列**: RabbitMQ / Redis Streams
- **向量数据库**: Qdrant / PGVector
- **关系数据库**: PostgreSQL
- **对象存储**: S3（或 MinIO 自建）
- **TTS**: Edge-TTS / ElevenLabs API
- **服务间通信**: REST（同步）+ 消息队列（异步 Pipeline）

## 微服务列表

| 服务 | 职责 | API 前缀 |
|------|------|---------|
| **API Gateway** | 统一入口、认证、限流、路由 | `/api/v1/` |
| **Ingestion Service** | 文档解析、分块、Embedding、入库 | `/api/v1/ingestion` |
| **Source Service** | 文档源元数据、文件夹管理 | `/api/v1/sources` |
| **RAG Service** | 语义检索 + LLM 问答生成 | `/api/v1/query` |
| **Audio Service** | 双人播客脚本生成 + TTS 合成 | `/api/v1/audio` |
| **Note Service** | 笔记 CRUD、标注、摘录关联 | `/api/v1/notes` |

## 数据流

1. 用户上传文档 → **Ingestion Service** 接收 → 解析 → 分块 → Embedding → 向量入库 + 文档块存 Postgres
2. **Source Service** 记录文档元数据，监听 Ingestion 完成事件更新状态
3. 用户提问 → **RAG Service** 语义检索 → Rerank → LLM 生成带引用的回答
4. 用户请求音频 → **Audio Service** 拉取文档摘要 → 生成双人脚本 → TTS 合成 → 存 CDN
5. **Note Service** 管理笔记 CRUD，关联到文档/文档块

## 微服务详细设计

以下各文件由 subagent 并行生成，每个文件对应一个微服务的完整设计。

- `docs/design/notebooklm/api-gateway.md`
- `docs/design/notebooklm/ingestion-service.md`
- `docs/design/notebooklm/source-service.md`
- `docs/design/notebooklm/rag-service.md`
- `docs/design/notebooklm/audio-service.md`
- `docs/design/notebooklm/note-service.md`
