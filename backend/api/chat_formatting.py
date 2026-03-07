import json


def _pick_text(item: dict, keys: list[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _format_question_block(title: str, questions: list[dict], target_keys: list[str]) -> str:
    if not questions:
        return ""
    lines = [title]
    for idx, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            continue
        q = _pick_text(item, ["question", "content", "text", "prompt", "stem"])
        target = _pick_text(item, target_keys)
        if not q:
            continue
        if target:
            lines.append(f"{idx}. {q}\n（目的：{target}）")
        else:
            lines.append(f"{idx}. {q}")
    return "\n".join(lines).strip()


def build_tool_student_text(tool_trace: list[dict]) -> str:
    blocks: list[str] = []
    for round_item in tool_trace:
        for call in round_item.get("tool_calls", []):
            name = call.get("name")
            raw_args = call.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else {}
                if not isinstance(args, dict):
                    args = {}
            except Exception:
                args = {}

            if name == "assess":
                questions = args.get("questions", [])
                if isinstance(questions, list):
                    block = _format_question_block(
                        "【诊断问题】",
                        questions,
                        ["purpose", "expected_concept", "target", "goal"],
                    )
                    if block:
                        blocks.append(block)
            elif name == "quiz":
                questions = args.get("questions", [])
                if not isinstance(questions, list):
                    for alt in ("items", "quiz", "quiz_items"):
                        alt_questions = args.get(alt)
                        if isinstance(alt_questions, list):
                            questions = alt_questions
                            break
                phase_id = args.get("phase_id")
                title = f"【阶段{phase_id}检测题】" if phase_id is not None else "【检测题】"
                if isinstance(questions, list):
                    block = _format_question_block(
                        title,
                        questions,
                        ["expected_concept", "purpose", "target", "goal"],
                    )
                    if block:
                        blocks.append(block)

    uniq: list[str] = []
    for block in blocks:
        if block not in uniq:
            uniq.append(block)
    return "\n\n".join(uniq).strip()


def format_answers_readable(tool_name: str, answers: list[dict]) -> str:
    lines = []
    if tool_name == "assess":
        lines.append("[诊断问题回答]")
    elif tool_name == "quiz":
        lines.append("[阶段检测回答]")
    else:
        lines.append("[工具问答回答]")
    for i, item in enumerate(answers, start=1):
        q = item.get("question", f"问题{i}") if isinstance(item, dict) else f"问题{i}"
        a = item.get("answer", "(未回答)") if isinstance(item, dict) else "(未回答)"
        lines.append(f"{i}. 问题：{q}")
        lines.append(f"   回答：{a}")
    return "\n".join(lines)


def format_answers(tool_name: str, answers: list[dict]) -> str:
    lines = []
    if tool_name == "assess":
        lines.append("学生的诊断回答：")
    elif tool_name == "quiz":
        lines.append("学生的检测回答：")
    else:
        lines.append("学生回答：")

    for i, item in enumerate(answers, start=1):
        q = item.get("question", f"问题{i}") if isinstance(item, dict) else f"问题{i}"
        a = item.get("answer", "(未回答)") if isinstance(item, dict) else "(未回答)"
        lines.append(f"{i}. 问题：{q}")
        lines.append(f"   回答：{a}")

    return json.dumps(
        {
            "status": "ok",
            "student_answers": "\n".join(lines),
        },
        ensure_ascii=False,
    )
