from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from .trace_logger import basic_action_strategy

from .llm_router import ChatBackend, chat_json_with_raw, describe_llm_error, dump_json, model_version_label
from .planner_validation import PlanValidationError, validate_plan, DEPENDENCIES, PRODUCES, DEPENDENCY_LABELS

CAP_CN = {
    "climate": "空调",
    "seat": "座椅",
    "entertainment": "影音娱乐",
    "ambient_light": "氛围灯",
    "navigation": "导航",
    "ecosystem": "生态服务",
    "notification_mode": "通知",
}

MODE_CN = {
    "auto_generate": "自动生成",
    "auto_execute": "自动执行",
    "conditional_auto": "条件自动",
    "confirm_execute": "确认后执行",
    "choose_execute": "选择后执行",
    "suggest_only": "仅建议",
}

PLANNER_SYSTEM_LEGACY = """你是车载人格行为映射引擎的Step 7：行为序列生成与输出模块。

你的任务是综合Step 1—6的结果、原子能力库、执行方式表及安全边界，生成原子能力调用序列。

你需要处理的输入包括：

1. Step 1：人格类型与用户自由描述的情境；
2. Step 2：MODEL 1输出的情境理解；
3. Step 3：人格主导功能查表结果；
4. Step 4：人格场景行动策略；
5. Step 5：目标人格描述及行动策略；
6. Step 6：人格交互维度判断；
7. behavior_blueprint：当前情境的能力域骨架；
8. capability_catalog：二级原子能力库；
9. execution_modes：执行方式表；

你需要完成：

1. 根据目标人格描述及行动策略、人格场景行动策略、情境目标、Must-do，判断需要调用的能力域；
2. 从对应能力域中选择具体二级原子能力；
3. 判断每个原子能力是required、conditional还是optional；
4. 补齐完成任务所需的必要前置动作，单个原子能力域可调用多次；
5. 根据真实依赖关系安排动作顺序；
6. 根据人格策略和交互维度调整执行方式、选项数量、确认方式、主动程度、参数和介入范围；
7. 分别说明情境与人格如何影响每个动作；
8. 输出不调用的能力域及原因。

决策优先级：

安全与法规
＞ 人格倾向
＞ 用户明确目标与硬约束
＞ Must-do核心任务
＞ 情境边界
＞ 可选体验增强

一、输入能力库结构

capability_catalog是唯一合法能力来源，其真实结构为：

{
  "capability_catalog": [
    {
      "capability": "能力域ID",
      "display_name": "一级能力域中文名称",
      "description": "该能力域的覆盖范围说明",
      "supported_needs": {"需求名称": 支撑权重0-1},
      "properties": {"风险/侵入性/可逆性/是否需要确认": 属性值},
      "atomic_abilities": [
        {
          "name": "二级原子能力名称",
          "definition": "该能力能够完成的具体动作",
          "core_params": ["允许使用的执行参数"],
          "service_sources": ["提供数据或完成执行的系统来源"]
        }
      ]
    }
  ]
}

字段含义：

1. capability：一级能力域ID；
2. display_name：一级能力域中文名称；
3. description：该能力域的覆盖范围；
4. supported_needs：该能力对各类需求的支撑权重；
5. properties：该能力的安全、侵入性、可逆性等属性；
6. atomic_abilities[].name：二级原子能力的唯一合法名称；
7. atomic_abilities[].definition：该原子能力实际完成的动作；
8. atomic_abilities[].core_params：该动作允许使用的核心参数；
9. atomic_abilities[].service_sources：提供数据或完成执行的系统来源。

一级能力域固定映射：

- climate：空调；
- entertainment：影音娱乐；
- ambient_light：氛围灯；
- navigation：导航；
- notification_mode：通知；
- seat：座椅；
- ecosystem：生态服务。

二、原子能力约束

1. capability只能从whitelist和capability_catalog中选择。
2. display_name必须与capability_catalog中的对应名称完全一致。
3. name只能从对应capability的atomic_abilities[].name中逐字选择。
4. capability与name必须形成能力库中真实存在的组合。
5. 禁止发明、翻译、缩写、概括或改写原子能力名称。
6. 禁止使用definition、行为目标、人格策略或场景策略充当name。
7. 一个behavior_actions元素只能表达一个二级原子能力。
8. 搜索、详情获取、状态查询、筛选、比较、选择、设置、规划、启动和进度查询必须分别输出。
9. 禁止将多个原子能力压缩成一个概括性动作。
10. behavior_blueprint只负责限制或提示能力域，不能新增原子能力：constraint=must的能力域必须调用，constraint=optional的能力域由你结合情境与人格决定是否调用。

三、情境与人格的作用边界

情境决定调用什么，包括：

1. 当前核心任务；
2. Must-do能力；
3. 完成任务所需的必要前置能力；
4. 允许调用的辅助能力；
5. 禁止或无需求的能力；
6. 前置条件；
7. 动作依赖关系；

人格决定如何调用，包括：

1. 主动或被动；
2. 提前或延后；
3. 自动生成、自动执行、确认后执行或选择后执行；
4. 提供几个选项；
5. 参数强度；
6. 信息呈现重点；
7. 介入范围；
8. 确认严格程度；
9. 是否保留用户自主选择；
10. 同一情境骨架内可选能力的调用顺序。

四、决策状态

decision_state只能是：

1. required
   完成当前核心任务必须调用，并且当前具备执行或生成所需的关键条件。

2. conditional
   属于任务所需动作，但当前仍缺少用户输入、前序结果、权限、触发阈值或其他前置条件。

3. optional
   用于增强体验，用户可以拒绝，不影响核心任务完成。

无法进入behavior_actions的能力放入no_call：

1. blocked
   受到安全、权限、能力缺失或关键条件限制，当前无法调用。

2. not_selected
   当前情境没有调用需求。

蓝图约束解读：capability_hints中的每个能力域带constraint标记。constraint=must表示当前必须涉及（场景骨架强制）；constraint=optional表示不违反安全规范、可由你结合情境与人格自行决定是否调用。capability_hints与optional_capabilities之外的能力域一律不得调用。

决策规则：

1. 缺少前置条件，但条件补齐后可以继续的动作，使用conditional。
2. 完全缺少执行依据或受到明确禁止的动作，放入no_call。
3. optional动作不得排在其依赖的required或conditional动作之前。
4. constraint=must的能力域必须至少有一个原子动作进入behavior_actions，且该动作的decision_state应为required或conditional。
5. constraint=optional的能力域自行决定是否调用：调用时decision_state可为optional（条件满足时也可为conditional）；不调用时放入no_call并说明具体判断。
6. 禁止调用capability_hints与optional_capabilities之外的能力域。
7. hard_rules可以把required、conditional或optional调整为blocked。
8. 不得为了体现人格而增加optional动作。
9. 同一能力域应尽量包含多个不同decision_state的原子动作。

五、执行方式

execution_mode只能是：

1. auto_generate
   自动完成搜索、读取、查询、分析、规划、筛选、比较、摘要或草稿生成，不改变车辆和外部服务状态。

2. auto_execute
   无需事前确认，直接改变车辆状态。
   仅适用于安全必需，或低风险、可撤销且已获得明确授权的动作。

3. conditional_auto
   满足明确阈值、权限、历史授权或触发条件后自动执行；条件不满足时不得执行。

4. confirm_execute
   系统提出一个明确动作及关键参数，用户确认后执行。

5. choose_execute
   系统提供两个或以上候选项，用户完成选择后执行对应动作。

6. suggest_only
   只提供建议，不调用实际能力，也不改变车辆或服务状态。

执行方式匹配规则：

1. 搜索、详情获取、历史记录读取、状态查询、分析、规划、筛选、比较、摘要和草稿生成使用auto_generate。
2. 提供多个地点、路线、内容或服务方案并等待选择时使用choose_execute。
3. 提出一个明确动作，并在用户同意后改变状态时使用confirm_execute。
4. 恢复历史设置时，只有存在明确历史记录和自动恢复授权，才能使用conditional_auto。
5. 支付、缴费、下单、预约、排队、订单修改、消息发送和电话回拨必须使用confirm_execute。
6. 对车辆或外部服务产生真实影响的动作不得使用auto_generate。
7. suggest_only表示完全不执行；如果用户同意后会改变状态，应使用confirm_execute。
8. 用户选择路线后启动导航，可以使用choose_execute。
9. 安全必需的前挡除雾可以使用auto_execute或conditional_auto。
10. 目的地未知时：
    - 地点搜索需要地点名称、地址或关键词作为前置条件；

六、动作依赖与顺序

不得机械套用单一固定顺序，必须根据所选原子能力之间的真实依赖关系排序。

通用规则：

1. 获取信息的动作先于使用该信息的动作；
2. 搜索先于详情查看；
3. 搜索和状态查询先于筛选；
4. 筛选先于比较；
5. 比较先于用户选择；
6. 对象选择先于目标设置；
7. 目的地设置先于路线规划；
8. 路线规划先于路线比较；
9. 路线比较先于路线选择；
10. 路线选择先于导航运行控制；
11. 导航启动后才能进行基于当前路线的预计到达时间获取；
12. 获取预计到达时间后才能判断迟到风险；
13. 创建订单先于支付；
14. 价格与优惠查询先于支付；
15. 通知优先级分类先于自动延后、静音或屏蔽；
16. 用户确认或选择节点必须位于真实状态改变之前。

典型导航链路：

地点搜索
→ 地点详情查看
→ 目的地设置
→ 路线规划
→ 路线比较
→ 路线选择
→ 导航运行控制
→ 预计到达时间获取
→ 迟到风险判断

其中：

1. 地点已经唯一明确时，可以省略地点搜索或地点详情查看；
2. 只有一条可行路线时，可以省略路线比较；
3. 用户已经明确指定路线偏好且规则允许时，可以简化路线选择；
4. 省略动作必须有输入事实或规则依据；
5. 不得仅为缩短输出而省略必要依赖。

典型开放式服务链路：

服务与地点搜索
→ 服务详情获取或实时资源状态查询
→ 候选服务筛选
→ 候选方案比较
→ 候选方案选择
→ 导航目的地设置
→ 导航路线规划
→ 导航路线比较
→ 导航路线选择
→ 导航运行控制

典型交易链路：

服务与地点搜索
→ 服务详情获取
→ 价格与优惠查询
→ 实时资源状态查询
→ 候选方案选择
→ 预约与排队管理或订单管理
→ 支付与缴费
→ 服务进度查询

七、执行参数约束

1. parameters_hint必须是结构化JSON对象。
2. parameters_hint中的参数名称只能来自所选原子能力的core_params。
3. 参数值只能来自：
   - 用户明确输入；
   - Step 2确认的情境事实；
   - 已授权的历史偏好；
   - 实时查询结果；
   - 安全规则允许的默认值。
4. unknown信息不得被默认值覆盖。
5. 缺少的关键参数应写入preconditions，不得虚构参数值。
6. parameters_hint中如需指定服务来源，只能使用所选原子能力service_sources中的值。
7. 未确定具体服务来源时，不得擅自指定高德、美团、大众点评或其他服务商。
8. 参数强度必须位于hard_rules允许范围内。
9. 选项数量由Step 6的option_count控制，同时受情境效率和安全要求限制。
10. 恢复历史偏好时，必须确认存在对应用户、历史记录和授权。
11. parameters_hint不得包含与该原子能力无关的参数。

八、字段要求

1. capability填写一级能力域ID。
2. display_name填写该能力域对应的中文名称。
3. name填写二级原子能力原名，与atomic_abilities[].name逐字一致。
4. decision_state填写required、conditional或optional。
5. intensity表示系统介入强度，不表示设备物理档位。

intensity定义：

- low：低介入，只提供必要信息或单一建议；
- medium：中度介入，调用辅助能力或增加一次确认；
- high：高介入，承担当前核心任务中的关键步骤。

6. preconditions填写尚未满足或执行前必须满足的条件；无前置条件时输出空数组。
7. details必须依据对应原子能力的definition，说明系统具体执行什么。
8. details不得宣称尚未满足条件的动作已经完成。
9. details禁止使用以下泛化表达：
   - 由模型基于材料生成；
   - 根据人格进行处理；
   - 提供相应服务；
   - 执行相关操作；
   - 优化用户体验；
   - 遵循人格策略。
10. scenario_influence只说明：
    - 情境为什么激活该动作；
    - 情境如何影响动作必要性和顺序；
    - 情境带来了什么限制或前置条件。
11. scenario_influence不得使用人格名称、荣格功能或人格策略代替情境依据。
12. persona_influence只说明：
    - 人格如何改变确认方式；
    - 人格如何改变选项数量；
    - 人格如何改变主动程度；
    - 人格如何改变参数、介入范围或执行顺序。
13. persona_influence禁止只写：
    - 符合INFP；
    - 符合ESTJ；
    - 体现Fi；
    - 体现Te；
    - 个人优先维护；
    - 外部目标编排；
    - 遵循人格策略。
14. persona_influence必须转化成可以观察的行为差异。
15. decision_basis只输出简短、可审计的决策依据，不输出隐藏思考过程。
16. decision_basis应指出主要依据来自核心目标、Must-do、未知信息、人格策略、交互维度或安全规则。
17. final_explanation使用2—4句话说明：
    - 情境形成了什么共同能力骨架；
    - 人格具体改变了哪些执行方式；
    - 人格没有改变哪些核心任务和安全边界。

九、不调用规则

1. whitelist中的每个未调用能力域都应进入no_call。
2. 当前情境没有需求时使用not_selected。
3. 受到安全、权限、能力或关键条件限制时使用blocked。
4. reason必须结合具体情境说明原因。
5. 禁止统一使用“未进入本轮流程骨架”作为所有不调用原因。
6. 不调用原因示例：
   - 当前车内温度适宜，没有温度或空气调节需求；
   - 用户处于时间压力情境，影音内容可能增加干扰；
   - 当前没有座椅不适或历史位置恢复需求；
   - 用户已有明确任务，不需要开放式生态服务探索。
7. 同一能力域已有原子动作进入actions时，不需要在no_call中重复列出该能力域。
8. 单个原子动作因条件不足未调用，但同域其他动作已调用时，可以在notes中说明。

十、输出前静默校验

输出前必须静默检查：

1. capability是否存在于whitelist；
2. capability是否存在于capability_catalog；
3. display_name是否与capability一致；
4. name是否逐字存在于该capability的atomic_abilities；
5. details是否符合对应definition；
6. parameters_hint是否只使用允许参数；
7. execution_mode是否符合动作类型；
8. decision_state是否与前置条件一致；
9. 动作依赖是否完整；
10. order是否从1开始连续递增；
11. scenario_influence与persona_influence是否分离；
12. 是否误用人格策略充当情境影响；
13. 是否补全了unknown信息；
14. 是否调用了capability_hints与optional_capabilities之外或安全禁止的能力域；constraint=must能力域是否全部进入behavior_actions；
15. 是否存在多个原子能力被压缩成一个动作；
16. 是否输出了任何旧字段或自创动作。

发现问题时：

1. 优先在能力库和规则允许范围内修正；
2. 无法合法修正的动作从behavior_actions删除；
3. 将限制或失败原因写入notes；
4. 不得通过发明新的动作名解决问题。

十一、输出限制

1. 只输出合法JSON。
2. 不输出Markdown代码块。
3. 不输出JSON以外的任何解释。
4. 不输出spoken_reply或任何车机语音台词。
5. 不输出O_Action_Plan。
6. 不输出O_Behavior。
7. 不输出scene_plan。
8. 不输出planner_output包装字段。
9. 不输出validated_actions。
10. 不输出箭头连接的英文动作摘要。
11. 不输出内部思考过程。
12. behavior_actions必须按照order从1开始连续排序。
13. 不得宣称尚未执行的动作已经完成。
14. 无足够条件生成动作时，behavior_actions可以为空，并在no_call和notes中说明原因。

严格输出以下结构：

{
  "behavior_actions": [
    {
      "capability": "navigation|climate|seat|entertainment|ambient_light|notification_mode|ecosystem",
      "display_name": "一级能力域中文名称",
      "name": "二级原子能力名称（必须逐字取自该capability在capability_catalog.atomic_abilities[].name中的值）",
      "decision_state": "required|conditional|optional",
      "intensity": "low|medium|high",
      "execution_mode": "auto_generate|auto_execute|conditional_auto|confirm_execute|choose_execute|suggest_only",
      "order": 1,
      "preconditions": [],
      "details": "具体执行内容",
      "parameters_hint": {},
      "scenario_influence": "情境对动作必要性、顺序和限制的影响",
      "persona_influence": "人格对执行方式、选项、确认或参数的影响",
      "decision_basis": "简短且可审计的决策依据"
    }
  ],
  "no_call": [
    {
      "capability": "能力域ID",
      "display_name": "一级能力域中文名称",
      "name": "*",
      "decision_state": "blocked|not_selected",
      "reason": "结合当前情境说明不调用原因"
    }
  ],
  "final_explanation": "用2—4句话说明情境骨架和人格影响。",
  "notes": [
    "关键前置条件、能力边界、降级情况或无法补全的信息"
  ]
}
"""

PLANNER_SYSTEM = (Path(__file__).resolve().parents[1] / "prompts" / "planner_system_v2.txt").read_text(encoding="utf-8")

STEP7_SCENARIO_FIELDS = (
    "task",
    "facts",
    "unknowns",
    "context",
    "needs",
    "emotion",
    "risk",
    "activated_interaction_dimensions",
)


def current_planner_system() -> str:
    return (Path(__file__).resolve().parents[1] / "prompts" / "planner_system_v2.txt").read_text(encoding="utf-8")


def _compact_json_prompt(value: str) -> str:
    """Keep the wire prompt small while preserving custom non-JSON prompts."""
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def build_step7_scenario_understanding(intent_output: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Project Step 2 into its compact planning contract; adapt older saved output."""
    source = intent_output or {}
    if "task" not in source and "core_goal" in source:
        source = {
            "task": {
                "core_goal": source.get("core_goal") or "",
                "explicit_intents": [source["explicit_intent"]] if source.get("explicit_intent") else [],
                "optional_goals": list(source.get("implicit_needs") or []),
                "constraints": list(source.get("task_constraints") or []),
                "must_do": list(source.get("must_do") or []),
            },
            "unknowns": list(source.get("unknowns") or []),
            "facts": list(source.get("key_facts") or []),
            "context": dict(source.get("context_overrides") or {}),
            "needs": dict(source.get("parsed_needs") or {}),
            "emotion": dict(source.get("emotion") or {}),
            "risk": dict(source.get("risk") or {}),
        }
    projected = {field: source[field] for field in STEP7_SCENARIO_FIELDS if field in source}
    projected.setdefault("activated_interaction_dimensions", [])
    return projected


def build_step7_capability_catalog(catalog: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Keep every available atom, projecting only its planning contract."""
    return [
        {
            "capability": cap["capability"],
            "display_name": cap.get("display_name"),
            "atomic_abilities": [
                {field: atom[field] for field in (
                    "name", "definition", "core_params", "parameter_schema"
                ) if field in atom}
                for atom in cap.get("atomic_abilities") or []
            ],
        }
        for cap in catalog
    ]


def _action_name(capability: str, scenario_id: str, persona_id: str) -> str:
    table = {
        "navigation": {
            "time_pressure": "规划效率路线",
            "pleasant_trip": "设置推荐地点并导航",
            "welcome_on_board": "设置目的地并启动导航",
            "fatigue_driving": "规划安全中止路线",
        },
        "ecosystem": {
            "pleasant_trip": "生成差异化地点选项" if persona_id == "INFP" else "生成推荐地点",
            "fatigue_driving": "搜索安全停车点",
        },
        "climate": {
            "temperature_control": "调整舱内温度",
            "welcome_on_board": "恢复常用温度",
        },
        "seat": {
            "cabin_comfort": "调整座椅舒适模式",
            "emotional_support": "恢复低负担舒适坐姿",
        },
        "notification_mode": {
            "time_pressure": "收束非关键通知",
            "pleasant_trip": "减少非紧急打扰",
            "fatigue_driving": "保留安全和导航提醒",
        },
        "entertainment": {
            "festival_celebration": "提供节庆内容建议",
            "entertainment_relax": "提供放松内容建议",
            "pleasant_trip": "提供内容建议",
        },
        "ambient_light": {
            "festival_celebration": "切换节庆氛围灯",
            "entertainment_relax": "切换轻松氛围灯",
        },
    }
    return table.get(capability, {}).get(scenario_id, CAP_CN.get(capability, capability))


def plan_from_blueprint(
    *,
    persona_id: str,
    scenario_id: str,
    whitelist: List[str],
    blueprint: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    blueprint = blueprint or {}
    hints = list(blueprint.get("capability_hints") or [])
    actions = []
    selected = set()
    for order, hint in enumerate(hints, start=1):
        cid = hint.get("capability")
        if hint.get("constraint") == "optional":
            # 规则回退只按必须域生成动作；可选域由 LLM 路径按情境决定
            continue
        if cid not in whitelist:
            continue
        selected.add(cid)
        actions.append(
            {
                "capability": cid,
                "display_name": hint.get("display_name") or CAP_CN.get(cid, cid),
                "name": (hint.get("name") or hint.get("action")) or _action_name(cid, scenario_id, persona_id),
                "details": hint.get("details") or hint.get("reason") or "按情境与人格策略执行",
                "intensity": hint.get("intensity") or "medium",
                "execution_mode": hint.get("execution_mode") or "confirm_execute",
                "order": order,
                "reason": hint.get("reason") or "来自流程蓝图的优先动作",
                "persona_influence": hint.get("persona_influence") or "",
                "scenario_influence": hint.get("scenario_influence") or "",
                "parameters_hint": hint.get("parameters_hint") or {},
            }
        )

    reasons = dict(blueprint.get("no_call_reasons") or {})
    no_call = []
    for cid in whitelist:
        if cid in selected:
            continue
        no_call.append({"capability": cid, "reason": reasons.get(cid) or "未进入本轮行为序列"})

    notes = list(blueprint.get("notes") or [])
    return {"behavior_actions": actions, "no_call": no_call, "notes": notes, "source": "blueprint"}


def normalize_interaction_dimensions(dimensions: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep option_count as a quantity, including when loading older review data."""
    normalized = dict(dimensions)
    if "scaled_1_to_5" in normalized:
        normalized["scaled_1_to_5"] = {
            key: value for key, value in (normalized["scaled_1_to_5"] or {}).items()
            if key != "option_count"
        }
    return normalized


def build_planner_input(
    *,
    persona_id: str,
    scenario_id: str,
    parsed: Mapping[str, Any],
    whitelist: List[str],
    rule_summary: List[str],
    flow_context: Mapping[str, Any],
    blueprint: Mapping[str, Any],
) -> Dict[str, Any]:
    # Saved review sessions can still contain the older, full catalog.
    planner_context = dict(flow_context)
    if "persona_description" in planner_context:
        planner_context["persona_description"] = basic_action_strategy(planner_context["persona_description"] or {})
    if "interaction_dimensions" in planner_context:
        dimensions = normalize_interaction_dimensions(planner_context["interaction_dimensions"] or {})
        policy = dimensions.get("interaction_policy")
        if isinstance(policy, dict):
            # Keep the full configuration in Step 6's audit; give the planner
            # only the selected requirements, not competing legacy defaults.
            planner_context["interaction_dimensions"] = {
                "mode": dimensions.get("mode"),
                "fixed_dimensions": policy.get("fixed_dimensions") or [],
                "active_dimensions": [item["dimension"] for item in policy.get("constraints") or []],
                "compiled_prompt": policy.get("compiled_prompt") or "",
            }
        else:
            planner_context["interaction_dimensions"] = dimensions
    if "capability_catalog" in planner_context:
        planner_context["capability_catalog"] = build_step7_capability_catalog(
            planner_context["capability_catalog"] or []
        )
    if parsed.get("explicit_request"):
        planner_context["explicit_request"] = dict(parsed["explicit_request"])
    return {
        "persona": persona_id,
        "scenario_id": scenario_id,
        "rule_summary": rule_summary[:20],
        "flow_context": planner_context,
    }


def expand_compact_demo_plan(raw, payload):
    """Expand wire aliases only; persona decisions/why remain model-generated."""
    if not isinstance(raw,dict) or not isinstance(raw.get('behavior_actions'),list):return raw
    import copy
    result=copy.deepcopy(raw)
    caps={c['capability']:c for c in payload['flow_context']['capability_catalog']}
    for i,a in enumerate(result['behavior_actions']):
        if not isinstance(a,dict) or 'c' not in a:continue
        if not {'c','n','m','p','d','why','basis'}<=set(a)<= {'c','n','m','p','d','why','basis','w'}:raise PlanValidationError('紧凑演示动作字段不完整')
        result['behavior_actions'][i]={'capability':a['c'],'display_name':caps.get(a['c'],{}).get('display_name',a['c']),
            'name':a['n'],'intensity':'medium','execution_mode':a['m'],'order':i+1,'preconditions':a.get('w',[]),
            'details':a['d'],'parameters_hint':a['p'],'scenario_influence':'共同虚拟情境与明确任务',
            'persona_influence':a['why'],'decision_basis':a['basis']}
    # Add only mechanical public-query prerequisites. Never invent user choices,
    # permissions, settings or a selected route. Raw model output stays in attempts.
    providers={'destination_candidates':('navigation','地点搜索'),
               'destination_resolved':('navigation','地点详情查看'),
               'content_candidates':('entertainment','内容搜索'),
               'service_candidates':('ecosystem','服务与地点搜索')}
    state=set();expanded=[];repairs=[]
    def append_action(action):
        if not isinstance(action,dict) or not {'capability','name','execution_mode'}<=set(action):raise PlanValidationError('演示动作格式不完整')
        key=(action['capability'],action['name'])
        for needed in DEPENDENCIES.get(key,()):
            if needed in state or needed not in providers:continue
            cid,name=providers[needed];cap=caps.get(cid,{})
            if not any(a['name']==name for a in cap.get('atomic_abilities',[])):continue
            append_action({'capability':cid,'display_name':cap.get('display_name',cid),'name':name,
                'intensity':'medium','execution_mode':'auto_generate','order':0,'preconditions':[],
                'details':'按共同演示设定'+name+'，为后续步骤提供可用候选或地址。',
                'parameters_hint':{},'scenario_influence':'共同任务的技术依赖',
                'persona_influence':'人格未改变此动作','decision_basis':'补齐前序查询依赖，不改变模型的目标选择'})
            repairs.append(name)
        action['order']=len(expanded)+1;expanded.append(action)
        if action['execution_mode']!='suggest_only':state.update(PRODUCES.get(key,set()))
    for action in result['behavior_actions']:append_action(action)
    result['behavior_actions']=expanded
    if repairs:result['notes']=list(result.get('notes') or [])+['编译补齐公共查询依赖：'+'、'.join(repairs)]
    return result


def run_planner(
    *,
    llm_backend: str,
    client: Optional[ChatBackend],
    persona_id: str,
    scenario_id: str,
    parsed: Mapping[str, Any],
    whitelist: List[str],
    rule_summary: List[str],
    flow_context: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    prompt_override: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    model_ver = model_version_label(llm_backend, client)
    payload_in = build_planner_input(
        persona_id=persona_id,
        scenario_id=scenario_id,
        parsed=parsed,
        whitelist=whitelist,
        rule_summary=rule_summary,
        flow_context=flow_context,
        blueprint=blueprint,
    )

    if client is None:
        plan = plan_from_blueprint(
            persona_id=persona_id,
            scenario_id=scenario_id,
            whitelist=whitelist,
            blueprint=blueprint,
        )
        return {
            "model_version": model_ver,
            "fallback": True,
            "input": payload_in,
            "output": plan,
        }

    # Receive long plans incrementally to avoid waiting for one buffered
    # gateway response. The transport still requires a complete answer before
    # JSON parsing and plan validation; transport failures are not retried here.
    if hasattr(client, "stream"):
        client.stream = True
    if hasattr(client, "race_n"):
        client.race_n = 1

    system_prompt = (prompt_override or {}).get("system_prompt") or current_planner_system()
    override_user_prompt = (prompt_override or {}).get("user_prompt")
    user_prompt = override_user_prompt if override_user_prompt is not None else _compact_json_prompt(dump_json(payload_in))
    raw_text = ""
    attempts = []
    clarification_guidance = (
        "当前是演示模式：不得返回needs_clarification或要求用户补充；自行补全必要的场景设定，在notes中以“演示假设：”标明，继续输出完整ready计划。"
        "假设不代表已获得位置、查询结果或授权，地址和路线依赖仍须安排前序查询与规划。"
        if flow_context.get("generation_mode") == "demo" else
        "缺信息且无法查询时使用needs_clarification、具体问题和受条件约束的执行方式。"
    )
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]
    try:
        for attempt in range(2):
            started = time.monotonic()
            raw_text = ""
            try:
                raw, raw_text = chat_json_with_raw(client, messages, max_tokens=2400 if flow_context.get('compact_demo') else 6000)
                if flow_context.get('compact_demo'):raw=expand_compact_demo_plan(raw,payload_in)
                out = {**validate_plan(raw, payload_in, whitelist), "source": "llm"}
                attempts.append({"attempt": attempt + 1, "status": "ok", "elapsed_seconds": round(time.monotonic() - started, 2),
                                 "raw_llm_text": raw_text, "messages": list(messages)})
                break
            except Exception as exc:
                attempts.append({"attempt": attempt + 1, "status": "failed", "elapsed_seconds": round(time.monotonic() - started, 2),
                                 "error": str(exc), "error_info": ({"category": "planner_validation", "message": str(exc)} if isinstance(exc, PlanValidationError) else describe_llm_error(exc)),
                                 "raw_llm_text": raw_text, "messages": list(messages)})
                if attempt or not isinstance(exc, PlanValidationError):
                    raise
                messages = messages + [{"role": "assistant", "content": raw_text},
                    {"role": "user", "content": dump_json({"validation_feedback": str(exc),
                     "dependency_contract": [{"action": name, "requires": [DEPENDENCY_LABELS[d] for d in deps],
                         "providers": {DEPENDENCY_LABELS[d]: [n for (c, n), produced in PRODUCES.items() if d in produced] for d in deps}}
                         for (cap, name), deps in DEPENDENCIES.items() if cap in whitelist],
                     "instruction": "校验器只返回首个错误，请同时核对所有动作参数和依赖，重新输出完整JSON。保留原任务、人格及权限；需要的查询结果应有前序真实查询动作，写入preconditions不等于已获取结果。" + clarification_guidance + "不编造地址坐标，不删除必要目标来通过校验。"})}]
        return {
            "model_version": model_ver,
            "fallback": False,
            "input": payload_in,
            "output": out,
            "prompt_used": {"system_prompt": system_prompt, "user_prompt": user_prompt},
            "raw_llm_text": raw_text,
            "attempts": attempts,
        }
    except Exception as exc:
        err_info = ({"category": "planner_validation", "message": str(exc),
                     "hint": "修正所列字段、参数或动作依赖后重新生成 Step 7。", "retryable": True}
                    if isinstance(exc, PlanValidationError) else describe_llm_error(exc))
        # A failed LLM call is not a valid personality plan. Keep the
        # diagnostic information, but never substitute blueprint actions.
        plan = {
            "behavior_actions": [],
            "source": "llm_error",
            "notes": [
            f"planner_fallback:{err_info['category']}",
            err_info["message"],
            f"hint:{err_info['hint']}",
            ],
            "llm_error": err_info,
        }
        return {
            "model_version": model_ver,
            "fallback": True,
            "error": str(exc),
            "error_info": err_info,
            "input": payload_in,
            "output": plan,
            "prompt_used": {"system_prompt": system_prompt, "user_prompt": user_prompt},
            "raw_llm_text": raw_text,
            "attempts": attempts,
        }
