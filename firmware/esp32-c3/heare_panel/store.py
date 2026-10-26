"""What survives a reset: the part token, the boot count, and the config.

Two stores live here. Small values sit in NVS under the ``heare`` namespace,
where powers of two and tokens belong; the configuration sits in
``/config.json``, where a person can read it and the portal can replace it in
one write. Every key is ten characters, inside the NVS limit of fifteen.

The machine token is never here. It exists for one knock during pairing and
must not survive a reboot; only the revocable part token does.
"""

try:
    import esp32
except ImportError:
    esp32 = None

from heare_panel import config as schema

NAMESPACE = "heare"
KEY_PART_TOKEN = "part_token"
KEY_PID = "device_pid"
KEY_BOOT_COUNT = "boot_count"
KEY_CONFIG_VER = "config_ver"
DEFAULT_PATH = "/config.json"
BLOB_MAX = 512


class StoreError(Exception):
    pass


class Store:
    def __init__(self, nvs=None, path=DEFAULT_PATH):
        if nvs is None:
            if esp32 is None:
                raise StoreError("NVS is unavailable")
            nvs = esp32.NVS(NAMESPACE)
        self.nvs = nvs
        self.path = path

    def _get(self, key):
        try:
            return self.nvs.get_blob(key)
        except TypeError:
            pass
        except OSError:
            return None
        buffer = bytearray(BLOB_MAX)
        try:
            length = self.nvs.get_blob(key, buffer)
        except OSError:
            return None
        if length is None:
            return bytes(buffer)
        return bytes(buffer[:length])

    def _set(self, key, value):
        self.nvs.set_blob(key, value)
        self.nvs.commit()

    def _erase(self, key):
        erase = getattr(self.nvs, "erase_key", None)
        if erase is not None:
            try:
                erase(key)
            except OSError:
                pass
        self.nvs.commit()

    def _text(self, key):
        data = self._get(key)
        if data is None:
            return None
        return bytes(data).decode()

    def _put_text(self, key, value):
        self._set(key, value.encode())

    def get_part_token(self):
        return self._text(KEY_PART_TOKEN)

    def set_part_token(self, token):
        if not isinstance(token, str) or not token:
            raise ValueError("part token must be a non-empty string")
        self._put_text(KEY_PART_TOKEN, token)

    def clear_part_token(self):
        self._erase(KEY_PART_TOKEN)

    def get_pid(self):
        return self._text(KEY_PID)

    def set_pid(self, pid):
        if not isinstance(pid, str) or not pid:
            raise ValueError("device pid must be a non-empty string")
        self._put_text(KEY_PID, pid)

    def boot_count(self):
        return self._number(KEY_BOOT_COUNT)

    def set_boot_count(self, count):
        self._put_number(KEY_BOOT_COUNT, count)

    def get_config_ver(self):
        return self._number(KEY_CONFIG_VER)

    def set_config_ver(self, version):
        self._put_number(KEY_CONFIG_VER, version)

    def _number(self, key):
        data = self._get(key)
        if data is None:
            return 0
        try:
            return int(bytes(data).decode())
        except ValueError:
            return 0

    def _put_number(self, key, value):
        number = int(value)
        if number < 0:
            raise ValueError("stored counter must be non-negative")
        self._put_text(key, str(number))

    def load_config(self):
        try:
            with open(self.path) as file:
                text = file.read()
        except OSError:
            return None
        return schema.loads(text)

    def save_config(self, config):
        """Validate and write ``config``, then remember its schema version.

        ``schema.dumps`` drops unknown keys, so a caller cannot smuggle a token
        into the file by adding one to the dict.
        """
        text = schema.dumps(config)
        with open(self.path, "w") as file:
            file.write(text)
        clean = schema.loads(text)
        self.set_config_ver(clean["version"])
        return clean
