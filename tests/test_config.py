"""Tests for llm_agent.config — user config file loading."""

import pytest

from llm_agent.config import load_config


class TestLoadConfig:
    def test_missing_file(self, tmp_path):
        result = load_config(str(tmp_path / "nonexistent.toml"))
        assert result == {}

    def test_valid_full_config(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text(
            'model = "opus"\n'
            'yolo = true\n'
            'timeout = 60\n'
            'thinking = "high"\n'
            'no_tui = true\n'
        )
        result = load_config(str(f))
        assert result == {
            "model": "opus",
            "yolo": True,
            "timeout": 60,
            "thinking": "high",
            "no_tui": True,
        }

    def test_partial_config(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text('model = "haiku"\n')
        result = load_config(str(f))
        assert result == {"model": "haiku"}

    def test_empty_file(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text("")
        result = load_config(str(f))
        assert result == {}

    def test_unknown_key_warned(self, tmp_path, capsys):
        f = tmp_path / "config.toml"
        f.write_text('model = "opus"\nfoo = "bar"\n')
        result = load_config(str(f))
        assert result == {"model": "opus"}
        assert "unknown config key 'foo'" in capsys.readouterr().err

    def test_wrong_type_warned(self, tmp_path, capsys):
        f = tmp_path / "config.toml"
        f.write_text('model = 42\n')
        result = load_config(str(f))
        assert result == {}
        assert "should be str" in capsys.readouterr().err

    def test_yolo_wrong_type(self, tmp_path, capsys):
        f = tmp_path / "config.toml"
        f.write_text('yolo = "yes"\n')
        result = load_config(str(f))
        assert result == {}
        assert "should be bool" in capsys.readouterr().err

    def test_timeout_wrong_type(self, tmp_path, capsys):
        f = tmp_path / "config.toml"
        f.write_text('timeout = "fast"\n')
        result = load_config(str(f))
        assert result == {}
        assert "should be int" in capsys.readouterr().err

    def test_invalid_toml(self, tmp_path, capsys):
        f = tmp_path / "config.toml"
        f.write_text("not valid [[[ toml")
        result = load_config(str(f))
        assert result == {}
        assert "could not parse" in capsys.readouterr().err

    def test_comments_and_whitespace(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text(
            '# My config\n'
            'model = "sonnet"  # the default\n'
            '\n'
            'timeout = 300\n'
        )
        result = load_config(str(f))
        assert result == {"model": "sonnet", "timeout": 300}
