# 需求规格说明书与工程研发规范：AI 评测中心

## 0. 模块能力地图 (Capability Map)

按职责边界与无环依赖拆分为 7 个可独立验证模块：

| Module id | 职责范围 | Depends on |
| --- | --- | --- |
| `auth-and-providers` | 控制台本地账号（JWT）、外部触发 `evk_*` API Key、以及**判别模型渠道**管理（支持 TypeSafe 官方、EMRank 等第三方 `/v1/decisions` 中转站、本地 Ollama `tev1:0.8b` 连通测试与网络代理策略） | — |
| `dimension-and-linter` | 评价维度（`noul` / `score` / `choice` 原子题与确定性规则）不可变版本管理、**题干静态质检器（Rubric Linter）**与评测套件门禁组装 | — |
| `corpus-and-curation` | 测试集管理、外部语料拉取并**单向调用数据脱敏中心 API** 清洗入库、`DEV / HOLDOUT` 分集隔离与低把握度优先的人工真值校准台 | `auth-and-providers` |
| `sut-adapters` | 可插拔被测适配器运行时：通用 E2E `HttpJsonAdapter` / `HttpSseAdapter`（支持采集 TTFT）与组件级 `ComponentHarnessAdapter`（支持调用外部被测服务录制 `Record` 快照与无缓存隔离回放 `Replay`） | — |
| `eval-kernel` | 同上下文多题合批编译器（单次 $\le 64$ 题、选项 $2\sim24$ 个）、统一 System One HTTP 客户端（直连/代理链回退 + WAF 403 剥代码块重试）、确定性规则引擎与**原始概率账本**计算器 | `auth-and-providers`, `dimension-and-linter` |
| `run-and-regression` | 异步评测批次调度器、双批次基线对比（含跨模型标尺变更告警与四选一**语义回归差异**判定）、以及基于原始概率账本的纯内存**零成本实时阈值校准**接口 | `corpus-and-curation`, `sut-adapters`, `eval-kernel` |
| `web-console` | Vue 3 独立控制台（渠道与适配器配置、维度编辑器与 Linter 实时诊断、语料校准与快照录制台、实验批次大盘、Baseline Diff 与阈值校准滑块） | `run-and-regression` |

**构建顺序**：`auth-and-providers`, `dimension-and-linter`, `sut-adapters` → `corpus-and-curation`, `eval-kernel` → `run-and-regression` → `web-console`

---

## 1. 显式前提与假设 (Explicit Assumptions)

1. 部署环境具备 Python 3.11+ 与 Node.js 20+（前端构建后由 FastAPI 单端口一体托管，默认端口 `18200`）。
2. 数据库采用独立 MySQL 8.0 Schema `kd_eval`，与 `kd_desensitize` 及任何被测业务库物理隔离，严禁跨库连表。
3. 当启用“线上语料拉取脱敏入库”功能时，通过 HTTP + `X-API-Key` 调用独立部署的 `data-desensitization-center`（默认 `http://127.0.0.1:18100/api/v1/mask/batch-inline`）；若直接导入已完成脱敏的离线 JSONL，也可独立运行。
4. 本地 Ollama 渠道需宿主机或局域网运行 **Ollama 0.35+** 且已拉取决策模型（如 `ollama pull tev1:0.8b`）。

---

## 2. 产品目标与核心用户场景 (Objective)

打造一套不绑定特定业务系统、零生成式评委幻觉的 AI 评测中心，覆盖两类核心评测闭环：
1. **端到端黑盒验收与跨版本回归（E2E Evals）**：基于沉淀并人工校准的黄金测试集（`HOLDOUT` 验真集），主动调用被测业务 HTTP/SSE 接口（如政务智能客服 `/ai/invoke`、`/ai/chat`），使用配置的判别模型渠道（云端 `Jev`、中转站 `emrank.tech` 或本地免费 `tev1:0.8b`）对事实忠实度、政策要件准确性、相关性与语义回归做确定性概率评分。
2. **内部组件离线回放与消融调优（Component Evals）**：通过受控 Harness 录制一次测试环境快照后，在 `DEV` 调优集上反复隔离回放路由、证据筛选或多智能体汇总组件（如 `FastRouter`、`SupervisorRouter`、`TeamRoutingRunner`、`AnswerQualityEvaluator`），快速对比不同提示词、阈值或模型的涨跌，并在报告台拖动滑块零成本寻找最优门禁阈值。

---

## 3. 技术栈 (Tech Stack)

- **后端**：Python 3.11+, FastAPI, Uvicorn, Pydantic v2, SQLAlchemy 2.0 + PyMySQL, `httpx`（支持异步连接池、SSE 流解析与代理链控制）, `jsonpath-ng`, `cryptography` (`AES-256-GCM`)。**不引入 `deepeval` 重依赖**。
- **前端**：Vue 3.4+, Vite 5+, TypeScript 5+, Pinia, Element Plus, ECharts（概率分布直方图与回归涨跌对比图）。
- **存储**：MySQL 8.0（独立库 `kd_eval`）。

---

## 4. 标准可执行命令 (Commands)

```powershell
# 后端依赖安装与本地开发启动 (f:\work_space\ai-eval-center\backend)
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 18200 --reload

# 后端单元测试与集成测试
.\.venv\Scripts\python -m pytest tests/ -v --tb=short

# 前端安装、开发联调与生产静态构建 (f:\work_space\ai-eval-center\web)
npm install
npm run dev
npm run build
```

---

## 5. 项目目录结构 (Project Structure)

```text
f:\work_space\ai-eval-center\
├── GLOSSARY.md                      # 领域词汇表
├── README.md                        # 项目入口说明
├── docs/                            # 需求、设计、数据库、API 规格与 SQL 脚本
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口与静态前端挂载
│   │   ├── config.py                # 环境变量配置
│   │   ├── auth/                    # JWT 与 API Key 鉴权
│   │   ├── providers/               # 统一 System One 客户端 (TypeSafe / EMRank / Ollama tev1)
│   │   ├── linter/                  # Jev 原子题静态质检器 (Rubric Linter)
│   │   ├── kernel/                  # 同 state 合批编译器、概率合成器、确定性规则引擎、内存重算器
│   │   ├── adapters/                # HttpJsonAdapter / HttpSseAdapter / ComponentHarnessAdapter
│   │   ├── ingestion/               # 只读源抽样器 + 对接 data-desensitization-center 客户端
│   │   ├── runner/                  # 异步实验批次执行器与 Baseline 语义回归对比器
│   │   ├── api/                     # REST 路由层
│   │   └── db/                      # SQLAlchemy 模型与仓储层
│   ├── tests/                       # 单元与集成测试 (含三类渠道 Mock 与重算精度断言)
│   └── requirements.txt
└── web/
    ├── src/
    │   ├── views/
    │   │   ├── providers/           # 判别模型渠道与被测适配器管理
    │   │   ├── dimensions/          # 评价维度工作台 (含实时题干 Linter 提示) 与套件配置
    │   │   ├── datasets/            # 测试集、脱敏导入、快照录制与 DEV/HOLDOUT 真值校准台
    │   │   ├── runs/                # 实验批次列表与发起向导
    │   │   └── reports/             # 单批详情、Baseline 语义回归 Diff、实时阈值校准滑块
    │   └── router/
    ├── package.json
    └── vite.config.ts
```

---

## 6. 代码风格约定 (Code Style)

评分合成与阈值重算函数必须设计为**无副作用纯函数**，使在线批次落库与报告页离线重算复用完全同一套算术逻辑：

```python
from typing import Mapping

def compute_choice_score(
    probabilities: Mapping[str, float],
    option_scores: Mapping[str, float | None],
) -> tuple[float | None, bool]:
    """计算 Choice 题期望得分；若不适用选项(score=None)概率之和 >= 0.5，返回 (None, False) 剔除出分母。"""
    na_prob = sum(p for opt, p in probabilities.items() if option_scores.get(opt) is None)
    if na_prob >= 0.5:
        return None, False
    applicable_prob = sum(p for opt, p in probabilities.items() if option_scores.get(opt) is not None)
    if applicable_prob <= 1e-9:
        return None, False
    weighted = sum(
        (probabilities.get(opt, 0.0) / applicable_prob) * score
        for opt, score in option_scores.items()
        if score is not None
    )
    return round(weighted, 6), True
```

---

## 7. 测试策略 (Testing Strategy)

- **单元测试 (`backend/tests/unit/`)**：
  - 验证统一 System One 请求体构造与三类渠道（TypeSafe `/v1/systemone`、EMRank `/v1/decisions`、Ollama `tev1:0.8b` `/v1/systemone`）响应解析、缺失 `confidence` 时的归一化兜底公式 $\max(0, \frac{n \cdot p_{\max} - 1}{n - 1})$、以及本地 Ollama 强制绕过代理与外网代理回退链逻辑；
  - 验证同 `state` 下超过 64 题时自动拆批、小于等于 64 题时单次合批及题号命名空间防冲突；
  - 验证静态 Linter 对 `Score` 档位非升序（好词在前差词在后）、复合多问句、超过 24 选项等反模式的拦截；
  - 验证确定性规则指标（JSONPath 匹配、集合召回率、回答中电话/金额/天数在参考材料中的精确核对）；
  - 验证拖动阈值重算结果与用新阈值重新跑批结果 100% 数值一致。
- **集成测试 (`backend/tests/integration/`)**：
  - 使用本地 `httpx.MockTransport` 模拟被测 E2E 接口、政务 Python Harness 端点、脱敏中心 API 与 EMRank/Ollama 决策端点，跑通“采集脱敏 → 校准 → 执行 → 回归对比 → 零成本重算”完整闭环。

---

## 8. 行为边界 (Boundaries)

- **Always do（必须遵守）**：
  - 每次 `ev_run` 必须不可变快照记录 `provider_id`、`pinned_endpoint_url` 与 `pinned_model_name`；当对比两个使用不同判别模型的批次时，接口必须返回 `cross_model_warning = true`；
  - 外部网络调用严格按渠道 `network_mode` 执行（`PROXY_FALLBACK` 按 `http://127.0.0.1:10808` → `http://127.0.0.1:7897` → 直连顺序尝试，`DIRECT` 强制 `trust_env=False` 直连）；
  - 从外部业务库抽样语料入库前，必须先经 `data-desensitization-center` 脱敏且 `pii_masked = 1` 方可落盘。
- **Ask first（需先确认）**：
  - 在 `kd_eval` 核心表上增删列或更改 `raw_answers_json` 账本协议；
  - 在被测项目生产环境开启任何评测录制端点。
- **Never do（绝对禁止）**：
  - 在 `ev_case` 或 `ev_run_case` 表结构中新增特定业务系统专属列；
  - 将 `HOLDOUT` 分集样本用于自动阈值寻优（自动推荐最佳阈值算法仅允许读取 `DEV` 分集）。

---

## 9. 可验证验收标准 (Success Criteria)

1. 在控制台配置并一键通过以下三类渠道的决策连通性测试：TypeSafe 官方、第三方 `https://emrank.tech/v1/decisions`、以及本地 `http://127.0.0.1:11434/v1/systemone` (`tev1:0.8b`)。
2. 对同一条用例挂载 3 个共享相同 `state_fields` 的评价维度（共 9 道原子题）时，内核合并为 **1 次** System One HTTP 请求完成判别，且 3 个维度的概率明细各自准确拆解入库。
3. 在 500 条用例的实验报告页调整任意维度阈值或权重时，`POST /api/v1/runs/{run_no}/recalculate` 纯内存重算响应耗时 < 100ms，外部模型请求次数为 `0`。
