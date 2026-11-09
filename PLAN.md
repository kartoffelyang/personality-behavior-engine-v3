# 人格行为映射引擎 — 实现计划

> 状态：已建目录骨架，待实现代码  
> 日期：2026-07-30  
> 约束：**仅允许修改** `/data/lzh/Benz/szy/` 下内容；禁止改动 `carsona_agent`、模型权重、vLLM 配置等外部路径。

---

## 0. 资源与硬约束

| 项 | 值 |
|---|---|
| 项目根目录 | `/data/lzh/Benz/szy/personality_behavior_engine` |
| 多性格服务 | `http://127.0.0.1:7106/v1`（OpenAI 兼容） |
| 服务进程 | pid=`2411317`，listen=`7106` |
| 选用模型 | **`carsona_simpo_4`**（只读调用，不改部署） |
| 服务目录（只读参考） | `/data/lzh/carsona_agent/toyota_double_0724` |
| 可写范围 | **仅** `/data/lzh/Benz/szy/**` |

模型调用约定（与仓库内现有脚本一致）：

```text
POST http://127.0.0.1:7106/v1/chat/completions
model: carsona_simpo_4
```

模型职责边界：

1. **ContextParser**：情境状态结构化解析（JSON Schema）
2. **ResponseGenerator**：人格化自然语言生成  
不负责选工具、不算分、不覆盖安全规则。

---

## 1. 第一阶段范围

- 人格：`INFP`、`ESTJ`
- 情境：`welcome_on_board`（上车迎宾）
- Case：10 个（见 `cases/welcome_cases.json`）
- 不训练 Planner、不用决策树、不用向量库
- 配置驱动 + 评分函数 + 确定性规则 + 有限可复现随机

每条 Case 输出 7 项：

1. 情境识别结果  
2. 人格功能激活结果  
3. 候选工具及得分  
4. 最终行为调用类型  
5. 行为执行序列  
6. 人格化场景方案与回复  
7. 决策轨迹 `decision_trace`

---

## 2. 目录落地（已创建）

```text
personality_behavior_engine/
├── PLAN.md                          # 本计划
├── config/
│   ├── personas.yaml
│   ├── cognitive_functions.yaml
│   ├── capabilities.yaml
│   ├── safety_rules.yaml
│   ├── scoring.yaml                 # 权重 / 阈值 / 随机参数
│   └── scenarios/
│       └── welcome_on_board.yaml
├── schemas/
│   ├── input.schema.json
│   ├── parsed_context.schema.json
│   ├── behavior_plan.schema.json
│   └── decision_trace.schema.json
├── engine/
│   ├── __init__.py
│   ├── context_parser.py            # 调 carsona_simpo_4
│   ├── persona_registry.py
│   ├── scenario_registry.py
│   ├── capability_registry.py
│   ├── rule_filter.py               # SafetyGuard + 显式指令 + 授权过滤
│   ├── scorer.py
│   ├── sampler.py
│   ├── behavior_composer.py
│   ├── response_generator.py        # 调 carsona_simpo_4
│   ├── llm_client.py                # 7106 封装
│   └── trace_logger.py
├── adapters/                        # 参数实例化，非真实车控
│   ├── navigation.py
│   ├── climate.py
│   ├── seat.py
│   ├── music.py
│   ├── ambient_light.py
│   └── notification_mode.py
├── prompts/
│   ├── context_parser.txt
│   └── response_generator.txt
├── cases/
│   └── welcome_cases.json
├── tests/
│   ├── test_safety.py
│   ├── test_explicit_request.py
│   ├── test_persona_difference.py
│   ├── test_randomness.py
│   └── test_new_capability.py
├── output/                          # Case 运行结果落盘
├── docs/
└── main.py                          # CLI：跑单条 / 跑 10 Case
```

---

## 3. 三张表 → 配置映射

| 原表 | 落盘文件 | 运行时用途 |
|---|---|---|
| 人格主导功能行动策略表 | `config/cognitive_functions.yaml` | 解释模板、功能命名；不参与 RAG |
| 人格场景行为策略表 | `config/scenarios/welcome_on_board.yaml` | 场景需求 bonus → 影响工具分 |
| 人格交互维度映射表 | `config/personas.yaml` | `function_scores` + `interaction` 连续参数 |

原则：表内容转成可执行数字/规则，**不把大段人格理论塞进 Prompt**。

---

## 4. 流水线（实现顺序）

```text
输入 JSON
  → ContextParser (LLM: carsona_simpo_4)
  → SafetyGuard（高风险直接安全方案）
  → ExplicitRequest（显式工具指令直通）
  → Capability 可用性 / 授权 / 冲突过滤
  → Scorer（需求×认知×场景×历史×交互 − 风险/侵入 + jitter）
  → Sampler（Top-K Softmax，可复现 seed）
  → BehaviorComposer（序列 + 参数档位）
  → ResponseGenerator (LLM: carsona_simpo_4)
  → DecisionTraceLogger → 落盘
```

### Phase A — 配置与 Schema（无 LLM）

1. 写齐 YAML：personas / cognitive_functions / capabilities / safety_rules / scoring / welcome_on_board  
2. 写 JSON Schema  
3. 实现 Registry 加载与校验  

### Phase B — 确定性核心（可单测）

1. `rule_filter`：安全覆盖、显式指令、授权过滤、防过度调用  
2. `scorer`：按文档权重实现并 clamp 到 `[0,1]`  
3. `sampler`：`seed = hash(request_id+persona+scenario_id+date_bucket)`  
4. `behavior_composer` + adapters 参数实例化  
5. `trace_logger` 结构化轨迹  

### Phase C — LLM 接入（只读 7106）

1. `llm_client.py`：`base=http://127.0.0.1:7106/v1`，`model=carsona_simpo_4`  
2. ContextParser：强制 JSON 输出 + Schema 校验；失败走规则兜底解析  
3. ResponseGenerator：只润色表达，禁止改工具/参数/伪造执行结果  
4. Prompt 中注入人格交互维度摘要，不注入完整三张表原文  

### Phase D — Cases 与验收

1. 写满 10 Case 输入（INFP / ESTJ 各跑一遍或按 Case 标注）  
2. `main.py run-cases` → `output/`  
3. 单元测试覆盖：安全、显式、人格差异、随机可复现、新能力注册不改 scorer  

---

## 5. 评分与规则（首期默认）

```python
FINAL_SCORE = clamp01(
    0.34 * need_match
  + 0.24 * cognitive_match
  + 0.16 * scenario_match
  + 0.10 * history_match
  + 0.10 * interaction_match
  + 0.06 * context_match
  - 0.12 * risk_cost
  - 0.08 * intrusion_cost
  + random_jitter   # 仅采样池内，且受 max_persona_randomness 限制
)
```

规则优先级（固定）：

1. 安全（`risk.overall >= 0.75`）→ 安全方案，人格只影响表达  
2. 显式指令 → 强制对应 capability  
3. 授权 / 可用性 / 状态冲突过滤  
4. 评分 + 阈值（`min_score_to_call=0.52`）  
5. 近分候选 Softmax 采样（窗口 `0.10`，温度 INFP=`0.16` / ESTJ=`0.10`）  

调用上限：`INFP≤2`，`ESTJ≤3`；全低分 → `no_tool + dialogue_only`。

---

## 6. 首期原子能力

`navigation` / `climate` / `seat` / `ambient_light` / `music` / `notification_mode`

每个能力在 `capabilities.yaml` 注册：`supported_needs`、`function_affinity`、`properties`、`parameter_presets`；adapter 只做参数实例化。

---

## 7. 10 Case 验收对照（摘要）

| Case | 关键预期 |
|---|---|
| 1 早晨通勤 | ESTJ 偏导航/效率；INFP 低干扰问候 |
| 2 夜间疲惫 | INFP 情绪+氛围/音乐+确认；ESTJ 导航回家+空调+减通知 |
| 3 即将迟到 | 双方优先导航；禁止无关情绪工具喧宾夺主 |
| 4 车内炎热 | 双方以 climate 为主 |
| 5 情绪低落 | INFP 少追问低干扰；ESTJ 可减通知/基础舒适 |
| 6 多人乘车 | 启用 group_balance，不能只按主驾人格覆盖他人 |
| 7 明确要音乐 | 显式指令 100% 走 music |
| 8 疲劳高风险 | 安全覆盖；禁只用音乐/氛围灯掩盖 |
| 9 节日 | 授权内轻量庆祝；未授权不泄露敏感日程 |
| 10 「走吧」 | 有目的地则基础导航；无则只问目的地；不随机氛围/音乐 |

硬验收：

- 显式指令优先率 100%  
- 安全覆盖正确率 100%  
- 同 seed 可复现；安全/显式不随机  
- 同场景 INFP vs ESTJ 至少在得分 / 执行模式 / 顺序上有稳定差异，且不靠无关工具硬造差异  

---

## 8. 实现注意事项

1. **只读外部资源**：可 `curl`/`requests` 调 7106；不改 yaml 部署、不杀进程、不写 peft。  
2. **LLM 失败兜底**：解析失败用规则从 utterance+context 抽最小 `parsed_context`，并在 trace 标记 `parser_fallback=true`。  
3. **evidence 约束**：只允许输入中可核对事实，禁止自由推理句。  
4. **随机**：仅近分候选池；日志必须含 `random_seed` / `sample_pool` / `sampled_tool`。  
5. **依赖**：尽量用标准库 + `PyYAML` + `requests`；配置文件放项目内，不改仓库根 `requirements.txt`（可在本项目加 `requirements.txt`）。  

---

## 9. 建议实现里程碑

| 里程碑 | 交付 | 预计 |
|---|---|---|
| M0 | 目录 + PLAN（当前） | 完成 |
| M1 | 全量 config + schemas + registries | 下一步 |
| M2 | scorer / filter / sampler / composer + 单测（可 mock parser） | |
| M3 | 接通 `carsona_simpo_4` 解析与回复 | |
| M4 | 10 Case 跑通 + `output/` 评审包 | |

---

## 10. 下一步（待你确认后开工）

确认本计划后，按 **M1 → M2 → M3 → M4** 在本目录内实现；默认先不写 Gradio，CLI + JSON 输出即可给评审。

待确认点（若无异议则按默认）：

1. 项目名保持 `personality_behavior_engine`  
2. Case 默认对 INFP、ESTJ **各跑一遍**（共 20 次推理）  
3. ContextParser 失败时启用规则兜底，不中断整批 Case  
4. 暂不做 Web UI  
