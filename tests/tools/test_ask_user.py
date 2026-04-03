"""Tests for ask_user tool."""

import json

import pytest

from llm_agent.display import set_display
from llm_agent.tools.ask_user import handle, log, NEEDS_SEQUENTIAL
from tests.conftest import MockDisplay


class TestAskUser:
    def test_needs_sequential(self):
        assert NEEDS_SEQUENTIAL is True

    def test_free_text(self):
        display = MockDisplay(ask_result="user typed this")
        set_display(display)
        result = handle({"question": "What color?"})
        assert result == "user typed this"
        assert len(display.asks) == 1
        assert display.asks[0][0] == "What color?"
        assert display.asks[0][1] is None  # no choices

    def test_with_choices(self):
        display = MockDisplay(ask_result="blue")
        set_display(display)
        choices = [
            {"label": "red", "description": "warm"},
            {"label": "blue", "description": "cool"},
        ]
        result = handle({"question": "Pick a color", "choices": choices})
        assert result == "blue"
        assert display.asks[0][1] == choices

    def test_numeric_answer_resolves_to_label(self):
        display = MockDisplay(ask_result="2")
        set_display(display)
        choices = [
            {"label": "red"},
            {"label": "blue"},
            {"label": "green"},
        ]
        result = handle({"question": "Pick", "choices": choices})
        assert result == "blue"

    def test_numeric_answer_out_of_range(self):
        display = MockDisplay(ask_result="99")
        set_display(display)
        choices = [{"label": "red"}, {"label": "blue"}]
        result = handle({"question": "Pick", "choices": choices})
        # Out of range numeric should be returned as-is
        assert result == "99"

    def test_numeric_answer_without_choices(self):
        display = MockDisplay(ask_result="42")
        set_display(display)
        result = handle({"question": "How many?"})
        # No choices, so numeric answer is just the raw answer
        assert result == "42"

    def test_missing_question(self):
        result = handle({})
        assert "error" in result

    def test_empty_question(self):
        result = handle({"question": ""})
        assert "error" in result

    def test_structured_questions_return_json_mapping(self):
        display = MockDisplay(
            ask_result={
                "language_choice": "2",
                "notes": "Keep the existing style",
            }
        )
        set_display(display)
        result = handle(
            {
                "questions": [
                    {
                        "header": "Language",
                        "id": "language_choice",
                        "question": "Which language should I use?",
                        "options": [
                            {"label": "Go", "description": "use the Go toolchain"},
                            {"label": "Python", "description": "use the existing Python stack"},
                        ],
                    },
                    {
                        "header": "Notes",
                        "id": "notes",
                        "question": "Anything else I should keep in mind?",
                    },
                ]
            }
        )

        assert json.loads(result) == {
            "answers": {
                "language_choice": "Python",
                "notes": "Keep the existing style",
            }
        }
        assert len(display.asks) == 1
        assert isinstance(display.asks[0][0], list)
        assert display.asks[0][0][0]["id"] == "language_choice"

    def test_structured_questions_can_normalize_label_text(self):
        display = MockDisplay(ask_result={"backend": "sqlite"})
        set_display(display)

        result = handle(
            {
                "questions": [
                    {
                        "id": "backend",
                        "question": "Which backend?",
                        "options": [
                            {"label": "Postgres", "description": "shared DB"},
                            {"label": "SQLite", "description": "local file"},
                        ],
                    }
                ]
            }
        )

        assert json.loads(result) == {"answers": {"backend": "SQLite"}}

    @pytest.mark.parametrize(
        ("params", "expected"),
        [
            (
                {
                    "questions": [
                        {"question": "Missing id"},
                    ]
                },
                "missing 'id'",
            ),
            (
                {
                    "questions": [
                        {"id": "bad id", "question": "Bad id"},
                    ]
                },
                "invalid id",
            ),
            (
                {
                    "questions": [
                        {"id": "one", "question": "One"},
                        {"id": "one", "question": "Duplicate"},
                    ]
                },
                "duplicate question id",
            ),
            (
                {
                    "question": "Legacy",
                    "questions": [{"id": "one", "question": "Structured"}],
                },
                "either 'questions' or legacy",
            ),
            (
                {
                    "questions": [
                        {"id": "one", "question": "One"},
                        {"id": "two", "question": "Two"},
                        {"id": "three", "question": "Three"},
                        {"id": "four", "question": "Four"},
                    ]
                },
                "at most three questions",
            ),
            (
                {
                    "questions": [
                        {
                            "id": "backend",
                            "question": "Which backend?",
                            "options": [
                                {"label": "Postgres", "description": "shared DB"},
                            ],
                        }
                    ]
                },
                "two to three options",
            ),
            (
                {
                    "questions": [
                        {
                            "id": "backend",
                            "question": "Which backend?",
                            "options": [
                                {"label": "Postgres"},
                                {"label": "SQLite", "description": "local file"},
                            ],
                        }
                    ]
                },
                "missing 'description'",
            ),
        ],
    )
    def test_structured_question_validation_errors(self, params, expected):
        result = handle(params)
        assert expected in result

    def test_log(self, mock_display):
        log({"question": "What should I do?"})
        assert len(mock_display.logs) == 1
        assert "What should I do?" in mock_display.logs[0]

    def test_log_truncates_long_question(self, mock_display):
        long_q = "x" * 200
        log({"question": long_q})
        assert len(mock_display.logs) == 1
        assert "..." in mock_display.logs[0]

    def test_log_structured_question_count(self, mock_display):
        log(
            {
                "questions": [
                    {"header": "Language", "id": "language", "question": "Which language?"},
                    {"header": "Scope", "id": "scope", "question": "What scope?"},
                ]
            }
        )
        assert len(mock_display.logs) == 1
        assert "2 questions" in mock_display.logs[0]
        assert "Language" in mock_display.logs[0]
