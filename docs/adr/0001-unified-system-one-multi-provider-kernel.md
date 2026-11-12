# 自建统一 System One 决策协议多渠道内核，不强绑定 DeepEval

TypeSafe 官方（`/v1/systemone`）、第三方决策中转站（如 EMRank `/v1/decisions`）与本地 Ollama 0.35+ 决策模型（`/v1/systemone` `tev1:0.8b` / `tev1:4b`）共享完全一致的 `{model, state, questions} -> {answers, usage}` JSON 决策协议。我们决定直接面向该协议构建可配置渠道的评分与合批内核，而不封装 `DeepEval`。这放弃了 `DeepEval` 现成的内置指标包装类，但换取了同上下文跨维度单请求合批（单次最多 64 题，节省 75%+ 输入 Token）、云端与本地 0.8B 轻量模型零代码切换，以及对边缘 WAF 重试和代理回退链的完全控制。
