# 当前方案与代码导航

## 完整处理顺序

1. `web/persona_demo.py` 接收情境，创建后台任务并记录阶段；前端轮询结果。预设与自由输入使用当前中立理解及原子能力决策链路。
2. `engine/kev_intent_atomic.py` 与 `engine/kev_preparation.py` 组织云端中立理解：提取用户目标、约束、需求，发现相关能力及共享地点/内容候选。资料池不按人格分开编造。
3. `engine/kev_input.py`、`engine/kev_personality.py` 结合人格描述、角色规则和情境生成紧凑输入；每次评分都显式带入人格，不依赖模型拥有对话记忆。
4. `engine/kev_demo.py` 通过本地 `8009/v1/systemone` 调用 KEV；分别对两种角色的动作与资源选项评分，保留原始响应。
5. `engine/kev_intent_atomic.py` 将原始分数与公开强化权重结合，决定可选动作；必须动作与依赖单独处理。动作综合分公式为 `sigmoid(logit(raw) + 4 × mean(tag_weight))`，可选动作阈值为 0.5。这不是修改 KEV 原始返回，而是额外规则层。
6. `engine/kev_resource_choice.py` 对地点/内容选项计算 `softmax(log(raw) + 6 × resource_fit)`。ESTJ 对效率、费用、确定性、结构赋予较高权重；INFP 对环境体验、情绪、新颖性、自主空间赋予较高权重。完整数值以模块中的 `WEIGHTS` 为准。
7. `engine/kev_preparation.py` 及原子能力编译器补齐参数、搜索/详情/选择/播放等依赖，并检查不允许的动作。两种角色仍必须服从共同目标、明确禁令及安全约束。
8. `engine/kev_final_reply.py` 用一次云端请求撰写两种角色的回复，引用已选对象及其费用、距离、环境等已知资料。检查未采用动作的执行承诺和来源外数字；失败时保留可解释的编译结果。
9. `web/static/persona-demo.js`、`kev-atomic.js`、`demo-display.js` 展示回复、选择理由、分值和采用差异；内部设定措辞在面向用户的展示中简化，原始审计保留。

## 配置地图

| 内容 | 位置 |
|---|---|
| API 地址、模型、Key、超时 | 页面 `/settings/models`；本机 `.local-cloud.json` |
| 原子能力与参数定义 | `config/capabilities.yaml` |
| 人格描述及功能栈 | `config/personas.yaml`、`config/cognitive_functions.yaml` |
| 角色强化文本 | `config/kev_personality_policies.json` |
| 行为强化权重 | `engine/kev_intent_atomic.py` 的 `WEIGHTS` |
| 地点与内容选择权重 | `engine/kev_resource_choice.py` |
| 场景库 | `config/persona_demo_cases.json`、`config/kev_showcase_cases.json` |
| 最终回复提示词 | `engine/kev_final_reply.py` 的 `SYSTEM` |
| 原版提示词和历史版本 | `prompts/`、`prompt_versions/` |
| API 兼容客户端与诊断 | `engine/llm_router.py`、`llm_client.py`、`llm_diagnostics.py` |

## 使用边界与延迟

端到端时间包括云端理解、候选准备、本地两角色评分、编译和云端表述；不能只用 KEV 推理耗时描述整条链路。缓存命中与首次生成的耗时不同。设置页支持独立调整情境理解与话术请求的总时限，失败结果会保留当前阶段。

这些角色是演示所定义的偏好，并非真实人格定律。用户给出明确要求时，合理结果可以一致；通过公开权重强化差异，并不能证明 KEV 自然学到了人格理论。地点/歌曲等资料来自本轮候选准备，而不是联网地图或音乐服务。
