# AI 评测中心 (AI Evaluation Center)

基于统一 **System One 决策协议**（原生兼容官方 TypeSafe `Jev`、第三方决策中转站如 EMRank `/v1/decisions`、以及本地 Ollama `tev1:0.8b` / `tev1:4b`）构建的通用端到端（E2E）与组件级（Component）评测平台。支持同上下文多维度单请求合批、可插拔被测适配器（SUT Adapter）、`DEV / HOLDOUT` 防过拟合语料治理、跨批次语义回归对比及基于**原始概率账本**的零成本实时阈值校准。

## 文档索引

- [GLOSSARY.md](./GLOSSARY.md)：领域统一词汇表
- [docs/01-requirements-and-spec.md](./docs/01-requirements-and-spec.md)：产品需求、模块能力地图（Capability Map）与工程研发规格
- [docs/02-architecture-and-design.md](./docs/02-architecture-and-design.md)：系统架构与核心内核详细设计（System One 三端协议、多题合批、静态 Linter、SUT 适配器与概率账本重算）
- [docs/03-database-design.md](./docs/03-database-design.md)：`kd_eval` 业务无关通用数据库 ER 模型与表结构说明
- [docs/04-api-specification.md](./docs/04-api-specification.md)：RESTful API 完整接口规格文档（含外部配套被测组件回放 Harness 协议契约）
- [docs/sql/01_schema.sql](./docs/sql/01_schema.sql)：MySQL 8.0 建库与 11 张核心表完整 DDL
- [docs/sql/02_seed_defaults.sql](./docs/sql/02_seed_defaults.sql)：预置三类判别模型渠道、政务 E2E/Component 被测适配器与默认评价维度/套件初始化脚本
- [docs/adr/](./docs/adr/)：关键架构决策记录（ADR）
