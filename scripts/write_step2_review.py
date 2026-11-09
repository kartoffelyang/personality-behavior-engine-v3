"""Assemble the inspected Step 2 exchanges into a readable local report."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'output/step2_probe_20260915_133035'
RETRY = ROOT / 'output/step2_probe_20260915_133312'
DEST = ROOT / 'output/step2_review_20260915'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    DEST.mkdir(exist_ok=True)
    summaries = read(MAIN / 'summary.json')
    if (RETRY / 'summary.json').exists():
        retry_summaries = read(RETRY / 'summary.json')
        summaries = [r for r in summaries if r['persona'] != 'ESTJ'] + retry_summaries
    output = read(MAIN / 'NO_MBTI_output.json')
    system = (MAIN / 'system_prompt.txt').read_text(encoding='utf-8')
    user = (MAIN / 'NO_MBTI_user_prompt.json').read_text(encoding='utf-8')
    (DEST / 'system_prompt.txt').write_text(system, encoding='utf-8')
    (DEST / 'user_prompt.json').write_text(user, encoding='utf-8')
    (DEST / 'output.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = [
        '# Step 2 提示词、链路与真实测试结果', '',
        '测试日期：2026-09-15。主展示结果为 NO_MBTI 组，调用实际配置的 gpt-5.6-terra，fallback=false。结果未人工改写。', '',
        '## 本次修改', '',
        '- 采用用户提供的 Step 2 系统提示词和 JSON 字段；提示词独立保存在 prompts/intent_system.txt。',
        '- 补充默认背景与实测数据的区别、可选早餐的条件、目的地名称未确认、授权边界与自主控制需求强度的区别。',
        '- 用户提示词仍带 persona，但仅透传；不加入人格策略、历史偏好、原子能力库或行为序列。',
        '- Step 2 调用温度为 0.1，最大输出长度由默认 1400 提高到 4000 token，避免长 JSON 被截断；返回后校验字段、类型和 0—1 评分。',
        '- 修正规则兜底中明确截止时间的场景识别。兜底仍明确标记 fallback，不作为模型成功结果。', '',
        '## 链路映射', '',
        '| Step 2 输出 | 下游处理 |',
        '|---|---|',
        '| cabin_temperature_c | 非 null 时映射到内部 cabin_temperature；本例 null，不把偏凉转换为数值 |',
        '| thermal_sensation | 保留原有体感描述，与默认 24℃分开 |',
        '| destination_reference | 传入 destination，同时保留 reference；新的未解析目的地不设置 destination_known=true，并清除旧行程估时 |',
        '| arrival_deadline | 保留原文边界，并设置 calendar_pressure=true |',
        '| parsed_needs / emotion / risk | 传入 parsed_context；保留风险文字和 visibility、time_pressure 数值 |',
        '| 全部 Step 2 输出 | 作为 flow_context.scenario_understanding 传到 Step 7 |', '',
        'Step 3–6 仍从原始 persona 读取人格信息。本次没有修改人格策略或 Step 7 提示词。', '',
        '## 验证范围', '',
        '20 项自动测试通过，覆盖字段适配、未知目的地、无效评分与类型、人格不参与代码层调制，以及 Step 2 输出到 Step 7 输入的完整传递。链路测试使用固定模型回复及规则规划器，不代表真实 Step 7 模型已验证。本次真实 API 调用只运行 Step 2。', '',
        '| 输入人格 | 实际模型 | fallback | 耗时（秒） | 场景 |',
        '|---|---|---|---|---|',
    ]
    for row in summaries:
        lines.append(f"| {row['persona']} | {row['model']} | {str(row['fallback']).lower()} | {row['seconds']} | {row['scenario_id']} |")
    lines += ['',
        '第一轮 INFP 成功，ESTJ 和 NO_MBTI 在 120 秒超时。完善自主控制需求的判定后，第二轮 INFP、NO_MBTI 成功，ESTJ 发生网络中断；其后单独重试 ESTJ。各次原始记录均保留在 output/step2_probe_* 文件夹中。重试只将验证客户端等待上限调到 240 秒，网站配置仍为原来的 120 秒，网关稳定性仍可能影响运行。', '',
        '## 对当前输出的判断', '',
        '- 主场景为 time_pressure；首次赴康源大厦、十点前到达、早餐不能明显延误均被识别。',
        '- 没有臆造温度或将前挡发白认定为已经起雾；原因和实际视野影响保留为未知。',
        '- 早餐保留为有条件的次要需求；没有输出开空调、启动导航等执行指令。',
        '- cabin_temperature_c、passenger_count、driving_state、weather 在 overrides 中保持 null；默认背景没有被抄成用户提供的事实。',
        '- 无历史恢复、自主控制诉求证据，restore_history、user_autonomy 都为 0。',
        '- 仍有可改进处：例如部分 must_do 仍偏宽泛；默认 parked 与实际状态未确认的区别需要下游继续保留。',
        '- INFP 与 NO_MBTI 的措辞、需求及风险分数仍有小幅差异。一次各跑一组不能证明模型完全人格中立，也不能把差异归因于人格。正式人格行为 POC 建议共用一份 Step 2 结果，再比较 Step 3–7；本次未改造对比流程。', '',
        '## 当前系统提示词（完整）', '', '```text', system.rstrip(), '```', '',
        '## 本例用户提示词（完整，NO_MBTI）', '', '```json', user, '```', '',
        '## 本例真实 Step 2 输出（完整，NO_MBTI）', '', '```json',
        json.dumps(output, ensure_ascii=False, indent=2), '```', '',
    ]
    for persona in ['INFP', 'ESTJ']:
        source = RETRY if persona == 'ESTJ' and (RETRY / 'ESTJ_audit.json').exists() else MAIN
        audit = read(source / f'{persona}_audit.json')
        result_path = DEST / f'{persona}_output.json'
        result_path.write_text(json.dumps(audit['output'], ensure_ascii=False, indent=2), encoding='utf-8')
        lines += [f'## {persona} 组记录', '', f"fallback={str(audit['fallback']).lower()}；源记录：{source.name}/{persona}_audit.json。", '',
                  '```json', json.dumps(audit['output'], ensure_ascii=False, indent=2), '```', '']
    report = DEST / 'Step2提示词与实测结果.md'
    report.write_text('\n'.join(lines), encoding='utf-8')
    print(str(report))


if __name__ == '__main__':
    main()
