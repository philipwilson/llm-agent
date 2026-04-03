"""ask_user tool: ask the user clarifying questions."""

import json
import re

from llm_agent.formatting import bold, dim

SCHEMA = {
    "name": "ask_user",
    "description": (
        "Ask the user clarifying questions when you need more information "
        "to proceed. Supports a legacy single question or one to three short "
        "structured questions with stable IDs. Use when the request is ambiguous, "
        "when choosing between approaches, or when you need a decision from the user."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Legacy single-question prompt.",
            },
            "choices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["label"],
                },
                "description": "Legacy optional choices for a single question.",
            },
            "questions": {
                "type": "array",
                "description": (
                    "Preferred structured mode. Ask one to three short questions "
                    "and receive answers keyed by stable IDs."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "header": {
                            "type": "string",
                            "description": "Optional short header shown above the question.",
                        },
                        "id": {
                            "type": "string",
                            "description": "Stable answer key such as language_choice or test_scope.",
                        },
                        "question": {
                            "type": "string",
                            "description": "The question to ask the user.",
                        },
                        "options": {
                            "type": "array",
                            "description": "Optional mutually exclusive choices; provide two to three.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["label", "description"],
                            },
                        },
                    },
                    "required": ["id", "question"],
                },
            },
        },
    },
}

NEEDS_SEQUENTIAL = True  # always runs on main thread (needs user input)

_DEFAULT_ANSWER = "(no answer provided)"
_QUESTION_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def log(params):
    from llm_agent.display import get_display

    questions = params.get("questions")
    if isinstance(questions, list) and questions:
        preview = f"{len(questions)} question"
        if len(questions) != 1:
            preview += "s"
        first = questions[0]
        first_label = first.get("header") or first.get("question", "")
        if first_label:
            if len(first_label) > 48:
                first_label = first_label[:45] + "..."
            preview += f" ({first_label})"
    else:
        question = params.get("question", "")
        preview = question[:80] + "..." if len(question) > 80 else question
    get_display().tool_log(f"  {bold('ask_user')}: {dim(preview)}")


LOG = log


def _normalize_choice_answer(answer, choices):
    if not isinstance(answer, str):
        return _DEFAULT_ANSWER
    answer = answer.strip() or _DEFAULT_ANSWER
    if not choices:
        return answer
    if answer.isdigit():
        idx = int(answer) - 1
        if 0 <= idx < len(choices):
            return choices[idx]["label"]
    folded = answer.casefold()
    for choice in choices:
        label = choice["label"]
        if label.casefold() == folded:
            return label
    return answer


def _validate_legacy_question(params):
    question = params.get("question", "")
    choices = params.get("choices")
    if not question:
        return None, "(error: 'question' is required)"
    if choices is not None and not isinstance(choices, list):
        return None, "(error: 'choices' must be an array)"
    normalized_choices = None
    if choices:
        normalized_choices = []
        for index, choice in enumerate(choices, 1):
            if not isinstance(choice, dict):
                return None, f"(error: choice {index} must be an object)"
            label = str(choice.get("label", "")).strip()
            description = choice.get("description")
            if not label:
                return None, f"(error: choice {index} is missing 'label')"
            normalized = {"label": label}
            if description is not None:
                normalized["description"] = str(description).strip()
            normalized_choices.append(normalized)
    return {"question": question, "choices": normalized_choices}, None


def _validate_structured_questions(params):
    raw_questions = params.get("questions")
    if raw_questions is None:
        return None, None
    if params.get("question") or params.get("choices"):
        return None, "(error: use either 'questions' or legacy 'question'/'choices', not both)"
    if not isinstance(raw_questions, list):
        return None, "(error: 'questions' must be an array)"
    if not raw_questions:
        return None, "(error: 'questions' must contain at least one question)"
    if len(raw_questions) > 3:
        return None, "(error: 'questions' supports at most three questions)"

    normalized_questions = []
    seen_ids = set()
    for index, item in enumerate(raw_questions, 1):
        if not isinstance(item, dict):
            return None, f"(error: question {index} must be an object)"
        question_id = str(item.get("id", "")).strip()
        if not question_id:
            return None, f"(error: question {index} is missing 'id')"
        if not _QUESTION_ID_RE.match(question_id):
            return None, (
                f"(error: question {index} has invalid id '{question_id}'; "
                "use letters, numbers, underscores, or hyphens)"
            )
        if question_id in seen_ids:
            return None, f"(error: duplicate question id '{question_id}')"
        seen_ids.add(question_id)

        question_text = str(item.get("question", "")).strip()
        if not question_text:
            return None, f"(error: question {index} is missing 'question')"

        header = str(item.get("header", "")).strip()
        options = item.get("options")
        normalized_options = None
        if options is not None:
            if not isinstance(options, list):
                return None, f"(error: question {index} 'options' must be an array)"
            if not 2 <= len(options) <= 3:
                return None, f"(error: question {index} must have two to three options)"
            normalized_options = []
            for option_index, option in enumerate(options, 1):
                if not isinstance(option, dict):
                    return None, (
                        f"(error: question {index} option {option_index} must be an object)"
                    )
                label = str(option.get("label", "")).strip()
                description = str(option.get("description", "")).strip()
                if not label:
                    return None, (
                        f"(error: question {index} option {option_index} is missing 'label')"
                    )
                if not description:
                    return None, (
                        f"(error: question {index} option {option_index} is missing 'description')"
                    )
                normalized_options.append(
                    {"label": label, "description": description}
                )

        normalized_questions.append(
            {
                "id": question_id,
                "header": header,
                "question": question_text,
                "options": normalized_options,
            }
        )
    return normalized_questions, None


def _format_structured_answers(questions, answers):
    ordered_answers = {
        question["id"]: answers.get(question["id"], _DEFAULT_ANSWER)
        for question in questions
    }
    return json.dumps({"answers": ordered_answers}, indent=2, ensure_ascii=True)


def handle(params):
    from llm_agent.display import get_display

    questions, error = _validate_structured_questions(params)
    if error:
        return error
    if questions is not None:
        answers = get_display().ask_user(questions)
        if not isinstance(answers, dict):
            return "(error: structured ask_user expected a mapping of answers)"
        normalized_answers = {
            question["id"]: _normalize_choice_answer(
                answers.get(question["id"], _DEFAULT_ANSWER),
                question.get("options"),
            )
            for question in questions
        }
        return _format_structured_answers(questions, normalized_answers)

    legacy_question, error = _validate_legacy_question(params)
    if error:
        return error
    answer = get_display().ask_user(
        legacy_question["question"], legacy_question["choices"]
    )
    return _normalize_choice_answer(answer, legacy_question["choices"])
