# 对话历史构建：将会话消息格式化为供 LLM 使用的历史文本
def build_history(messages, max_entries=3, max_length_per_msg=300, role_map=None):
    """把最近若干轮格式化为查询改写所需的紧凑历史文本。"""
    if role_map is None:
        role_map = {"user": "用户", "assistant": "助手"}
    recent_messages = messages[-max_entries:] if messages else []
    parts = []
    for msg in recent_messages:
        role_label = role_map.get(msg.get("role"), msg.get("role"))
        content = msg.get("content", "")[:max_length_per_msg]
        parts.append(f"{role_label}：{content}")
    return "\n".join(parts)
