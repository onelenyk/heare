"""The config is the device's memory of where it belongs; these are its rules.

The happy path is one round trip; the rest pins every refusal to an exact
message and proves the two secrets (Wi-Fi password, pair code) never leave
through ``repr`` or the portal's view.
"""

from __future__ import annotations

import json

import pytest
from heare_panel import config

PASSWORD = "correct horse"
CODE = "ABCD2345"


def sample(**overrides):
    base = {
        "version": 1,
        "wifi": {"ssid": "kitchen", "password": PASSWORD},
        "brain": {"host": "192.168.1.10", "port": 9790},
        "device": {"name": "kitchen"},
    }
    for key, value in overrides.items():
        base[key] = value
    return base


def with_wifi(**changes):
    wifi = dict(sample()["wifi"])
    wifi.update(changes)
    return sample(wifi=wifi)


def with_brain(**changes):
    brain = dict(sample()["brain"])
    brain.update(changes)
    return sample(brain=brain)


def with_device(name):
    return sample(device={"name": name})


def test_round_trip_keeps_every_field():
    clean = config.validate(sample())
    assert config.loads(config.dumps(clean)) == clean


def test_dumps_is_deterministic_and_ordered():
    first = config.dumps(sample())
    second = config.dumps(sample())
    assert first == second
    assert first == (
        '{"version": 1, "wifi": {"ssid": "kitchen", "password": '
        '"correct horse"}, "brain": {"host": "192.168.1.10", "port": 9790}, '
        '"device": {"name": "kitchen"}}'
    )


def test_pair_is_optional_and_kept_when_present():
    assert "pair" not in config.validate(sample())
    clean = config.validate(sample(pair={"code": CODE}))
    assert clean["pair"] == {"code": CODE}
    assert "pair" in json.loads(config.dumps(clean))


def test_pair_code_is_normalised_to_upper():
    clean = config.validate(sample(pair={"code": " abcd2345 "}))
    assert clean["pair"] == {"code": CODE}


def test_unknown_fields_are_ignored():
    dirty = sample(extra={"nested": True}, tokens={"part": "secret"})
    dirty["wifi"]["channel"] = 6
    clean = config.validate(dirty)
    assert set(clean) == {"version", "wifi", "brain", "device"}
    assert set(clean["wifi"]) == {"ssid", "password"}
    assert "tokens" not in config.loads(config.dumps(dirty))
    text = config.dumps(dirty)
    assert "secret" not in text and "extra" not in text


def test_missing_port_defaults_to_9790():
    brain = {"host": "192.168.1.10"}
    clean = config.validate(sample(brain=brain))
    assert clean["brain"]["port"] == config.DEFAULT_PORT


def test_empty_port_from_form_defaults_to_9790():
    clean = config.validate(with_brain(port=""))
    assert clean["brain"]["port"] == config.DEFAULT_PORT


def test_loads_rejects_bad_json():
    with pytest.raises(config.ConfigError, match="config is not valid JSON"):
        config.loads("{")


def test_loads_rejects_non_object():
    with pytest.raises(config.ConfigError, match="config must be a JSON object"):
        config.loads("[]")


def test_version_other_than_one_is_refused():
    with pytest.raises(config.ConfigError, match="config version must be 1"):
        config.validate(sample(version=2))
    with pytest.raises(config.ConfigError, match="config version must be 1"):
        config.validate(sample(version=True))


@pytest.mark.parametrize("ssid", ["", "x" * 33, 7, None])
def test_ssid_validation(ssid):
    with pytest.raises(
        config.ConfigError, match="wifi ssid must be 1-32 characters"
    ):
        config.validate(with_wifi(ssid=ssid))


@pytest.mark.parametrize("password", ["short", "x" * 64])
def test_password_length_validation(password):
    with pytest.raises(
        config.ConfigError,
        match="wifi password must be 8-63 characters or empty",
    ):
        config.validate(with_wifi(password=password))


def test_password_may_be_empty_for_an_open_network():
    assert config.validate(with_wifi(password=""))["wifi"]["password"] == ""


def test_password_error_does_not_repeat_the_password():
    bad = "short"
    with pytest.raises(config.ConfigError) as caught:
        config.validate(with_wifi(password=bad))
    assert bad not in str(caught.value)


@pytest.mark.parametrize(
    "host",
    [
        "",
        "brain.local",
        "http://192.168.1.10",
        "192.168.1",
        "192.168.1.256",
        "192.168.1.01",
        "192.168.1.10:9790",
        "::1",
    ],
)
def test_brain_host_must_be_an_ipv4_literal(host):
    with pytest.raises(config.ConfigError, match="brain host must be an IPv4 address"):
        config.validate(with_brain(host=host))


@pytest.mark.parametrize("port", [0, 65536, -1, "http", True, 3.14])
def test_brain_port_range(port):
    with pytest.raises(config.ConfigError, match="brain port must be 1-65535"):
        config.validate(with_brain(port=port))


@pytest.mark.parametrize("name", ["", "Kitchen", "kitchen_sink", "x" * 25, 42])
def test_device_name_shape(name):
    with pytest.raises(
        config.ConfigError, match=r"device name must match \[a-z0-9-\]\{1,24\}"
    ):
        config.validate(with_device(name))


@pytest.mark.parametrize("code", ["", "ABCD234", "ABCD23456", "ABCD234O", 7])
def test_pair_code_shape(code):
    with pytest.raises(
        config.ConfigError,
        match="pair code must be 8 characters from the pair alphabet",
    ):
        config.validate(sample(pair={"code": code}))


def test_error_messages_do_not_repeat_secrets():
    leaked_password = "s3cr3t!"
    with pytest.raises(config.ConfigError) as caught:
        config.validate(with_wifi(password=leaked_password))
    assert leaked_password not in str(caught.value)

    leaked_code = "WXYZ234O"
    with pytest.raises(config.ConfigError) as caught:
        config.validate(sample(pair={"code": leaked_code}))
    assert leaked_code not in str(caught.value)

    with pytest.raises(config.ConfigError) as caught:
        config.validate(with_brain(host="brain.local"))
    assert PASSWORD not in str(caught.value)


def test_repr_hides_password_and_pair_code():
    clean = config.validate(sample(pair={"code": CODE}))
    text = repr(clean)
    assert PASSWORD not in text
    assert CODE not in text
    assert "***" in text


def test_public_view_hides_password_and_pair_code():
    view = config.public_view(sample(pair={"code": CODE}))
    text = json.dumps(view)
    assert PASSWORD not in text
    assert CODE not in text
    assert "password" not in text
    assert view["wifi"] == {"ssid": "kitchen"}
    assert view["brain"] == {"host": "192.168.1.10", "port": 9790}
    assert view["device"] == {"name": "kitchen"}


def test_merge_builds_a_config_from_the_portal_form():
    form = {
        "ssid": "kitchen",
        "password": PASSWORD,
        "brain_host": "192.168.1.10",
        "brain_port": "9790",
        "name": "kitchen",
    }
    clean = config.merge({}, form)
    assert clean == config.validate(sample())


def test_merge_ignores_unknown_form_keys_and_keeps_the_base():
    form = {"name": "study", "extra": "ignored", "__proto__": "no"}
    clean = config.merge(sample(), form)
    assert clean["device"]["name"] == "study"
    assert clean["wifi"]["password"] == PASSWORD


def test_merge_defaults_a_missing_port():
    form = {
        "ssid": "kitchen",
        "password": PASSWORD,
        "brain_host": "192.168.1.10",
        "name": "kitchen",
    }
    assert config.merge({}, form)["brain"]["port"] == config.DEFAULT_PORT


def test_merge_removes_pair_when_the_code_is_empty():
    clean = config.merge(sample(pair={"code": CODE}), {"pair_code": ""})
    assert "pair" not in clean


def test_merge_rejects_a_non_mapping_form():
    with pytest.raises(config.ConfigError, match="form must be a mapping"):
        config.merge({}, "ssid=kitchen")


def test_identity_from_a_fixed_mac_is_stable():
    first = config.identity("a1b2c3d4")
    second = config.identity("a1b2c3d4")
    assert first == second
    assert first == {
        "channel": "oled-a1b2",
        "pid": "oled-a1b2@esp32c3-a1b2",
        "host": "esp32c3-a1b2",
    }


def test_identity_from_a_full_mac_uses_its_last_two_bytes():
    assert config.identity("AA:BB:CC:DD:EE:FF") == {
        "channel": "oled-eeff",
        "pid": "oled-eeff@esp32c3-eeff",
        "host": "esp32c3-eeff",
    }


def test_identity_uses_a_bare_four_digit_tag_as_given():
    assert config.identity("07A1")["pid"] == "oled-07a1@esp32c3-07a1"


@pytest.mark.parametrize("mac", ["", "abc", "a1b2c3z4", None, 1234])
def test_identity_rejects_a_bad_tag(mac):
    with pytest.raises(
        config.ConfigError, match="mac must contain at least 4 hex digits"
    ):
        config.identity(mac)
