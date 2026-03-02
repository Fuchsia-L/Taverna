from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load_soul(filename: str = "teacher_socratic.md") -> str:
    """Load a teacher soul document from the prompts folder."""
    filepath = PROMPTS_DIR / filename
    return filepath.read_text(encoding="utf-8")


def build_system_prompt(
    teacher_soul: str,
    student_profile: str = "",
    current_topic: str = "",
    summary: str = "",
    current_state: str = "",
    global_memory_summary: str = "",
    project_context: str = "",
    conversation_objective: str = "",
    prior_conversation_summaries: str = "",
) -> str:
    """
    Assemble the full system prompt from layers.
    Empty slots are automatically skipped.
    """
    sections = [
        ("【教师设定】", teacher_soul),
        ("【全局记忆摘要】", global_memory_summary),
        ("【项目上下文】", project_context),
        ("【学生背景】", student_profile),
        ("【当前课题】", current_topic),
        ("【本会话目标】", conversation_objective),
        ("【前序会话摘要】", prior_conversation_summaries),
        ("【历史摘要】", summary),
        ("【当前状态】", current_state),
    ]

    parts = []
    for label, content in sections:
        if content and content.strip():
            parts.append(f"{label}\n{content}")

    return "\n\n".join(parts)
