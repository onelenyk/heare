"""The file is the one place every distribution agrees on a setting."""

from heare.config import Settings


def test_the_port_is_a_setting(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("api_port = 8080\n", encoding="utf-8")
    assert Settings(path).api_port == 8080


def test_without_a_file_the_port_is_the_default(tmp_path):
    assert Settings(tmp_path / "none.toml").api_port == 9790


def test_set_writes_the_port(tmp_path):
    path = tmp_path / "config.toml"
    Settings(path).set("api_port", 8081)
    assert Settings(path).api_port == 8081
