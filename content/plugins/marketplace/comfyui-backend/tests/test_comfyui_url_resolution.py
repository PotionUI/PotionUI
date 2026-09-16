import unittest
from unittest.mock import patch

from backend.comfyui_config import ComfyUIBackendConfig, resolve_comfyui_endpoint
from backend.pipes.comfyui.main import ComfyUIPipe, _resolve_comfyui_endpoint as pipe_resolve_endpoint

CASES = [
    ("comfyui", 8188, False, "http://comfyui:8188", "ws://comfyui:8188"),
    ("comfy.internal", 8188, False, "http://comfy.internal:8188", "ws://comfy.internal:8188"),
    ("comfy.internal:8188", 9999, False, "http://comfy.internal:8188", "ws://comfy.internal:8188"),
    ("[::1]", 8188, False, "http://[::1]:8188", "ws://[::1]:8188"),
    ("::1", 8188, False, "http://[::1]:8188", "ws://[::1]:8188"),
    ("https://gateway.example/comfy", 8188, False, "https://gateway.example/comfy", "wss://gateway.example/comfy"),
    ("192.168.1.100", 8188, True, "https://192.168.1.100:8188", "wss://192.168.1.100:8188"),
]


class TestResolveComfyUIEndpoint(unittest.TestCase):
    def test_config_and_pipe_resolvers_agree_on_every_case(self):
        for host, port, secure, expected_http, expected_ws in CASES:
            with self.subTest(host=host):
                self.assertEqual(
                    resolve_comfyui_endpoint(host, port, secure), (expected_http, expected_ws)
                )
                self.assertEqual(
                    pipe_resolve_endpoint(host, port, secure), (expected_http, expected_ws)
                )

    def test_backend_config_urls_use_the_shared_resolver(self):
        config = ComfyUIBackendConfig(
            id="docker-comfyui", name="Docker ComfyUI", host="comfyui", port=8188, secure=False
        )
        self.assertEqual(config.get_base_url(), "http://comfyui:8188")
        self.assertEqual(config.get_ws_url(), "ws://comfyui:8188/ws")

    def test_pipe_urls_accept_the_docker_hostname(self):
        pipe = ComfyUIPipe({
            "host": "comfyui",
            "port": 8188,
            "workflow_file": "",
            "field_mappings": [],
            "node_manipulations": [],
            "timeout": 30,
            "client_id": None,
            "secure": False,
        })
        http_base, ws_base = pipe._comfyui_urls()
        self.assertEqual(http_base, "http://comfyui:8188")
        self.assertEqual(ws_base, "ws://comfyui:8188")


class _FakeSetting:
    def __init__(self, key, value):
        self.setting_key = key
        self.setting_value = value


class TestAdminImportDefaultBaseUrl(unittest.TestCase):
    def test_default_host_accepts_a_hostname_and_reads_default_secure(self):
        from backend.api import _read_comfyui_default_connection

        settings = [
            _FakeSetting("default_host", "comfyui"),
            _FakeSetting("default_port", "8188"),
            _FakeSetting("default_secure", "true"),
        ]

        with patch("backend.api.PluginRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_plugin_settings.return_value = settings
            host, port, secure = _read_comfyui_default_connection()

        self.assertEqual(resolve_comfyui_endpoint(host, port, secure)[0], "https://comfyui:8188")


if __name__ == "__main__":
    unittest.main()
