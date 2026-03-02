"""
Tool schemas for the OpenAI function calling API.
These are passed in the `tools` parameter of chat.completions.create().
"""

TEACHING_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "assess",
            "description": (
                "向学生提出诊断性问题，评估其当前对该主题的理解水平。"
                "在新对话开始时使用，或在需要重新评估学生水平时使用。"
                "如果已有学生的学习摘要/记忆，可以选择不调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "诊断题内容"
                                },
                                "purpose": {
                                    "type": "string",
                                    "description": "这道题用来检测什么能力/知识点"
                                }
                            },
                            "required": ["question", "purpose"]
                        },
                        "description": "诊断题列表（1-5题）"
                    }
                },
                "required": ["questions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": (
                "制定或更新教学计划。系统会根据会话类型自动分流："
                "planning 会话生成项目级 learning_path，learning/review/standalone 会话生成会话级教学阶段。"
                "将学习目标拆分为若干有序阶段。"
                "每个阶段有明确的小目标。可以在教学过程中随时重新调用此工具来调整计划"
                "（如跳过阶段、插入新阶段、调整顺序）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "本次学习的总目标"
                    },
                    "phases": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "integer",
                                    "description": "阶段编号（从1开始）"
                                },
                                "title": {
                                    "type": "string",
                                    "description": "阶段标题"
                                },
                                "objective": {
                                    "type": "string",
                                    "description": "该阶段的具体学习目标"
                                }
                            },
                            "required": ["id", "title", "objective"]
                        },
                        "description": "教学阶段列表，按教学顺序排列"
                    }
                },
                "required": ["goal", "phases"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "advance",
            "description": (
                "标记当前阶段为已完成，推进到下一个阶段。"
                "仅在确认学生已达成当前阶段目标后调用。"
                "调用后会触发当前阶段的对话压缩。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "completed_phase": {
                        "type": "integer",
                        "description": "已完成的阶段编号"
                    },
                    "summary": {
                        "type": "string",
                        "description": "该阶段的学习小结（学生掌握了什么、哪里卡壳过、最终理解程度）"
                    },
                    "move_to": {
                        "type": "integer",
                        "description": "下一个要进入的阶段编号"
                    }
                },
                "required": ["completed_phase", "summary", "move_to"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "quiz",
            "description": (
                "向学生出检测题，验证当前阶段的学习成果。"
                "与 assess 的区别：assess 用于开头摸底，quiz 用于阶段中或结尾检测。"
                "老师自行决定何时出题、出什么题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phase_id": {
                        "type": "integer",
                        "description": "检测对应的阶段编号"
                    },
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "检测题内容"
                                },
                                "expected_concept": {
                                    "type": "string",
                                    "description": "期望学生展示的理解/能力"
                                }
                            },
                            "required": ["question", "expected_concept"]
                        },
                        "description": "检测题列表（1-3题）"
                    }
                },
                "required": ["phase_id", "questions"]
            }
        }
    }
]

# Reserved for future off-topic branching flow.
# Do not register this schema to TEACHING_TOOLS yet.
# BRANCH_TOOL_SCHEMA = {
#     "type": "function",
#     "function": {
#         "name": "branch",
#         "description": "Create a branch conversation for off-topic exploration.",
#         "parameters": {
#             "type": "object",
#             "properties": {
#                 "title": {"type": "string"},
#                 "objective": {"type": "string"},
#             },
#             "required": ["title"],
#         },
#     },
# }
