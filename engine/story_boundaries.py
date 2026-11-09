"""Conservative checks for explicit contradictions, not full semantic proof."""
import re


class StoryBoundaryError(ValueError):
    pass


def check_story_boundaries(story, payload):
    actions = {a['id']: a for a in payload.get('behavior_sequence', [])}
    warnings = []
    for paragraph in story.get('paragraphs', []):
        if not isinstance(paragraph, dict):
            warnings.append('部分故事没有动作引用，未完成执行边界检查。')
            continue
        for segment in paragraph.get('segments', []):
            if not isinstance(segment, dict):
                continue
            text = segment.get('text', '')
            refs = segment.get('action_ids', [])
            if not isinstance(refs, list) or any(type(i) is not int or i not in actions for i in refs):
                warnings.append('故事片段的动作引用无效，需人工核对执行边界。')
                continue
            if not refs:
                warnings.append('部分片段未绑定动作，仅能人工核对其执行边界。')
                continue
            modes = {actions[i].get('execution_mode') for i in refs}
            # Only reject an explicit execution claim for an isolated suggestion.
            # A mixed segment may legitimately execute another referenced action.
            if modes == {'suggest_only'}:
                claim = re.search(r'(?:助手|系统|它)(?:将|会|已经|已)(?:自动|临时|直接)?(?:开启|关闭|静音|设置|调整|执行|恢复|发送|启动)|(?:后[，,]?\s*才|才会)(?:自动|临时|直接)?(?:开启|关闭|静音|设置|执行|恢复|发送|启动)', text)
                if claim and not re.search(r'不|未|禁止|避免', text[max(0, claim.start()-6):claim.start()]):
                    raise StoryBoundaryError(f'动作{refs}仅允许建议，故事却承诺执行；保留建议，不补执行或恢复。')
            elif 'suggest_only' in modes:
                warnings.append('建议与执行动作在同一片段中，需人工确认没有混淆权限。')
            if re.search(r'(?:助手|系统|它)(?:已经|已)(?:为用户)?(?:开启|关闭|设置|发送|支付|启动)', text):
                raise StoryBoundaryError('故事将未执行的计划写成了已经完成')
            for mode, pattern, message in (
                ('confirm_execute', r'确认|同意|授权|许可', '保留用户确认'),
                ('choose_execute', r'用户.*(?:选|决定)|由.*(?:选|决定)|选择权', '保留用户选择'),
                ('conditional_auto', r'只有|如果|若|待|前提|条件|确认|在.*(?:后|时)|后.*才', '保留执行前提'),
            ):
                if mode in modes and not re.search(pattern, text):
                    # Absence of a keyword is not proof of a contradiction.
                    warnings.append(f'动作{refs}需核对是否充分{message}。')
    return list(dict.fromkeys(warnings))
