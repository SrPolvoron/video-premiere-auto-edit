from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from autoeditor_local.util import AutoEditorError
from autoeditor_local.vision import LocalModel, load_runtime


@pytest.fixture
def local_server(tmp_path):
    state = {"requests": [], "answer": {"segments": []}, "redirect": False, "finish_reason": "stop"}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if state["redirect"]:
                self.send_response(302)
                self.send_header("Location", "https://example.invalid/leak")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            answer = {"choices": [{"message": {"content": json.dumps(state["answer"])},
                                    "finish_reason": state["finish_reason"]}]}
            self.wfile.write(json.dumps(answer).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = {"endpoint": f"http://127.0.0.1:{server.server_port}/v1", "model_revision": "test-double",
              "context": 8192, "max_tokens": 512, "request_timeout": 10}
    try:
        with LocalModel(config, tmp_path / "not-used.log") as model:
            yield model, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_sends_timestamped_frames_only_to_local_server(local_server, tmp_path):
    model, state = local_server
    path = tmp_path / "frame.jpg"
    Image.new("RGB", (64, 64)).save(path)
    assert model.describe([(0, path), (1, path)], 2) == []
    request = state["requests"][0]
    content = request["messages"][1]["content"]
    images = [item["image_url"]["url"] for item in content if item["type"] == "image_url"]
    assert len(images) == 2 and all(item.startswith("data:image/jpeg;base64,") for item in images)
    assert request["response_format"] == {"type": "json_object"}
    assert "api_key" not in request


def test_model_refuses_redirect(local_server):
    model, state = local_server
    state["redirect"] = True
    with pytest.raises(AutoEditorError, match="redirect"):
        model.chat("test", "test")


def test_model_rejects_truncated_json(local_server):
    model, state = local_server
    state["finish_reason"] = "length"
    with pytest.raises(AutoEditorError, match="truncated"):
        model.chat("test", "test")


def test_prompt_compiler_validated(local_server):
    model, state = local_server
    state["answer"] = {"max_pov_fraction": 0.2, "preferred_tags": ["landscape"]}
    assert model.intent("Prioritize landscape") == state["answer"]
    state["answer"] = {"shell": "not allowed"}
    with pytest.raises(AutoEditorError):
        model.intent("test")


def test_runtime_missing_files(tmp_path):
    config = tmp_path / "model.toml"
    config.write_text('[runtime]\nserver="missing.exe"\nmodel="missing.gguf"\nmmproj="missing.gguf"\n')
    with pytest.raises(AutoEditorError, match="not found"):
        load_runtime(config)


def test_existing_server_requires_revision(tmp_path):
    config = tmp_path / "model.toml"
    config.write_text('[runtime]\nendpoint="http://127.0.0.1:9999/v1"\n')
    with pytest.raises(AutoEditorError, match="model_revision"):
        load_runtime(config)
