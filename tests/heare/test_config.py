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


def test_the_base_is_this_machine_unless_the_file_names_another(tmp_path):
    assert Settings(tmp_path / "none.toml").base == "http://127.0.0.1:9790"


def test_a_url_in_the_file_is_where_the_brain_is(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('url = "https://heare.onelenyk.dev/"\n', encoding="utf-8")
    assert Settings(path).base == "https://heare.onelenyk.dev"


def test_a_part_knocks_with_the_machine_key_when_there_is_one(tmp_path):
    master = tmp_path / "token"
    master.write_text("master", encoding="utf-8")
    machine = tmp_path / "machine_token"
    machine.write_text("machine", encoding="utf-8")
    alone = Settings(path=tmp_path / "none.toml", api_token_file=master)
    paired = Settings(path=tmp_path / "none.toml", api_token_file=master,
                      machine_token_file=machine)
    assert alone.secret == "master"
    assert paired.secret == "machine"
