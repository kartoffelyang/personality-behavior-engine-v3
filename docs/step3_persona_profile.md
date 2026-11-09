# STEP3：人格描述、主导功能与判断方式

更新日期：2026-09-18。

本步骤从 `config/personas.yaml` 的 `step3_profile` 查表，只输出当前人格的人格描述、主导功能代号及名称、功能含义、判断方式。三段正文 ESTJ 为 130 字，INFP 为 137 字（含标点）。不输出分数及前四项排序，不增加模型调用。

页面通过三项中文说明展示；行为序列输入使用 `flow_context.persona_profile`。STEP4 的通用行动策略与 STEP5 的场景策略继续承担行动层描述。旧记录从 STEP7 续跑时，重新查取当前 STEP3 描述并同步本轮审阅输出；不修改原记录对象。历史静态记录保持其原生成内容。

## 内容依据

- 人格整体描述：[Myers & Briggs Foundation，The 16 MBTI Personality Types](https://www.myersbriggs.org/my-mbti-personality-type/the-16-mbti-personality-types/)
- 主导功能含义与类型对应：[Type Dynamics: Processes](https://www.myersbriggs.org/unique-features-of-myers-briggs/type-dynamics-processes/)
- 思维、情感与判断偏好的定义：[The Preferences](https://www.myersbriggs.org/my-mbti-personality-type/the-mbti-preferences/)

人格及功能描述是上述理论的中文摘要；判断方式是为本项目提炼的设计描述，不是来源逐字引文，也不是已验证的行为预测规律。Fi 指人格主体的价值判断，不能据此推定用户的具体价值、偏好或授权。来源说明保留在本文，不重复发送给行为模型。
