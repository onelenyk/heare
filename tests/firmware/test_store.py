import pytest
from heare_panel import config as schema
from heare_panel import store as store_module
from heare_panel.store import Store

from tests.firmware.fakes import FakeNVS


def _config():
    return {
        "version": 1,
        "wifi": {"ssid": "home", "password": "secret123"},
        "brain": {"host": "192.168.1.10", "port": 9790},
        "device": {"name": "kitchen"},
    }


class BufferNVS(FakeNVS):
    def get_blob(self, key, buffer=None):
        value = super().get_blob(key)
        if buffer is None:
            raise TypeError("get_blob expects a buffer")
        buffer[: len(value)] = value
        return len(value)


def test_default_nvs_is_the_heare_namespace(monkeypatch, tmp_path):
    created = {}

    class FakeEsp32:
        def NVS(self, namespace):
            nvs = FakeNVS(namespace)
            created["namespace"] = namespace
            created["nvs"] = nvs
            return nvs

    monkeypatch.setattr(store_module, "esp32", FakeEsp32())

    store = Store(path=str(tmp_path / "config.json"))

    assert created["namespace"] == "heare"
    assert store.nvs is created["nvs"]


def test_a_store_without_nvs_or_esp32_raises(monkeypatch):
    monkeypatch.setattr(store_module, "esp32", None)

    with pytest.raises(store_module.StoreError):
        Store()


def test_part_token_round_trip_and_clear(fake_nvs):
    store = Store(fake_nvs)

    assert store.get_part_token() is None

    store.set_part_token("part-abc123")

    assert store.get_part_token() == "part-abc123"
    assert fake_nvs.blobs["part_token"] == b"part-abc123"
    assert fake_nvs.commits == 1

    store.clear_part_token()

    assert store.get_part_token() is None
    assert "part_token" not in fake_nvs.blobs
    assert fake_nvs.commits == 2


@pytest.mark.parametrize("token", ["", None, 5, b"bytes"])
def test_part_token_must_be_a_non_empty_string(fake_nvs, token):
    with pytest.raises(ValueError):
        Store(fake_nvs).set_part_token(token)


def test_device_pid_round_trip(fake_nvs):
    store = Store(fake_nvs)

    assert store.get_pid() is None

    store.set_pid("oled-a1b2@esp32c3-a1b2")

    assert store.get_pid() == "oled-a1b2@esp32c3-a1b2"
    assert fake_nvs.blobs["device_pid"] == b"oled-a1b2@esp32c3-a1b2"


@pytest.mark.parametrize("pid", ["", None, 5])
def test_device_pid_must_be_a_non_empty_string(fake_nvs, pid):
    with pytest.raises(ValueError):
        Store(fake_nvs).set_pid(pid)


def test_boot_counter_starts_at_zero_and_survives_a_new_store(fake_nvs):
    store = Store(fake_nvs)

    assert store.boot_count() == 0

    store.set_boot_count(3)

    assert store.boot_count() == 3
    assert fake_nvs.blobs["boot_count"] == b"3"
    assert Store(fake_nvs).boot_count() == 3


def test_a_corrupt_boot_counter_reads_as_zero(fake_nvs):
    fake_nvs.set_blob("boot_count", b"not-a-number")

    assert Store(fake_nvs).boot_count() == 0


def test_config_version_round_trip(fake_nvs):
    store = Store(fake_nvs)

    assert store.get_config_ver() == 0

    store.set_config_ver(1)

    assert store.get_config_ver() == 1
    assert fake_nvs.blobs["config_ver"] == b"1"


def test_counters_cannot_be_negative(fake_nvs):
    with pytest.raises(ValueError):
        Store(fake_nvs).set_boot_count(-1)

    with pytest.raises(ValueError):
        Store(fake_nvs).set_config_ver(-1)


def test_config_file_round_trip_and_version(fake_nvs, tmp_path):
    path = tmp_path / "config.json"
    store = Store(fake_nvs, path=str(path))

    assert store.load_config() is None

    saved = store.save_config(_config())

    assert saved == _config()
    assert path.exists()
    assert store.load_config() == _config()
    assert store.get_config_ver() == 1
    assert fake_nvs.commits == 1


def test_config_file_drops_token_fields(fake_nvs, tmp_path):
    path = tmp_path / "config.json"
    store = Store(fake_nvs, path=str(path))
    config = _config()
    config["part_token"] = "part-secret"
    config["machine_token"] = "machine-secret"

    store.save_config(config)

    text = path.read_text()
    assert "part-secret" not in text
    assert "machine-secret" not in text
    assert "part_token" not in text
    assert "machine_token" not in text


def test_save_config_rejects_invalid_input_without_touching_the_file(fake_nvs, tmp_path):
    path = tmp_path / "config.json"
    store = Store(fake_nvs, path=str(path))

    with pytest.raises(schema.ConfigError):
        store.save_config({"wifi": {}})

    assert not path.exists()
    assert store.get_config_ver() == 0
    assert fake_nvs.commits == 0


def test_load_config_propagates_a_corrupt_file(fake_nvs, tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")

    with pytest.raises(schema.ConfigError):
        Store(fake_nvs, path=str(path)).load_config()


def test_every_nvs_key_fits_the_fifteen_character_limit(fake_nvs):
    store = Store(fake_nvs)

    store.set_part_token("part")
    store.set_pid("pid")
    store.set_boot_count(1)
    store.set_config_ver(1)

    assert fake_nvs.blobs
    assert all(len(key) <= 15 for key in fake_nvs.blobs)


def test_store_reads_a_two_argument_nvs(tmp_path):
    nvs = BufferNVS()
    writer = Store(nvs, path=str(tmp_path / "config.json"))
    writer.set_part_token("part-abc")
    writer.set_pid("oled-a1b2@esp32c3-a1b2")
    writer.set_boot_count(4)
    writer.set_config_ver(1)

    reader = Store(nvs, path=str(tmp_path / "config.json"))

    assert reader.get_part_token() == "part-abc"
    assert reader.get_pid() == "oled-a1b2@esp32c3-a1b2"
    assert reader.boot_count() == 4
    assert reader.get_config_ver() == 1


def test_store_has_no_machine_token_slot():
    assert not hasattr(Store, "get_machine_token")
    assert not hasattr(Store, "set_machine_token")
    assert not hasattr(Store, "clear_machine_token")
