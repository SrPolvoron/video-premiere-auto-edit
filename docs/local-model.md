# Local model setup and first GPU experiment

## Candidate, not a verified hardware benchmark

The initial candidate is the official **Qwen3-VL-4B-Instruct-GGUF** release, with:

- `Qwen3VL-4B-Instruct-Q4_K_M.gguf` (approximately 2.5 GB on disk).
- `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` (approximately 454 MB on disk).

Obtain them from the [official model repository](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/tree/main),
read its model card and license, and use a compatible [llama.cpp](https://github.com/ggml-org/llama.cpp)
build with Qwen3-VL multimodal support. The model is a candidate for an 8 GB RTX 4060, not a
promise that every context length, image batch, runtime build and offload combination will fit.

**Disk size is not VRAM usage.** Weights, visual encoder, KV cache, inference buffers and image
tokens all consume memory. The code does not download or redistribute any of these components.

A CUDA-enabled `llama-server.exe` requires its matching runtime libraries and a compatible NVIDIA
driver. Keep all files required by that build together; copying only one EXE may not be sufficient.
See the upstream [multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)
and [server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

## Layout

```text
autoeditor-local/
  model.local.toml           # ignored: machine-specific paths/settings
  tools/llama.cpp/
    llama-server.exe         # plus that distribution's runtime DLLs
  models/
    Qwen3VL-4B-Instruct-Q4_K_M.gguf
    mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf
  work/route/
```

Copy `examples/model.local.example.toml` to the repository root, then edit its paths. Paths are
relative to the configuration file itself. On Linux use the corresponding `llama-server` binary.
Do not mix a visual projector from a different model.

`doctor --runtime model.local.toml` validates locations and reports `nvidia-smi` information when
available. **It does not load the model, run CUDA inference or prove that the configuration fits.**

## First experiment

Create a small project with one short tripod preparation take and one brief riding/nature take.
Do not begin with hours of footage. After importing the files:

```powershell
.\.venv\Scripts\autoeditor.exe init .\work\trial --media-root "E:\Videos\Trial"
.\.venv\Scripts\autoeditor.exe ingest .\work\trial
.\.venv\Scripts\autoeditor.exe --verbose analyze .\work\trial `
  --backend llama --runtime .\model.local.toml --max-windows 2
```

This deliberately pauses after two newly completed windows. Rerun without `--max-windows` to
finish. `catalog --include-partial` permits inspection before completion; normal planning does
not use partial results. Runtime diagnostics are in the project's ignored `logs/llama.log`.

Invalid segment annotations get one recovery request using integer millisecond timestamps,
with per-window bounds enforced through llama.cpp's JSON-schema grammar. The application
also validates the response independently, including segment order and anchor bounds.
The recovery end limit is rounded down by less than one millisecond; invalid original
responses are rejected, not clamped. If recovery still fails, the window remains failed
and rerunning the same command retries it while reusing completed windows. This recovery
does not change the initial prompt or invalidate existing successful analyses.

Check that the log reports GPU offload rather than CPU-only execution. Record runtime version,
model revision, settings, actual peak VRAM and elapsed time locally. GPU performance and actual
semantic accuracy were not measured while generating this repository.

## If memory is insufficient

Close applications that use the GPU, including Premiere during analysis. Try context 4096,
`--frames 4`, and `--longest 256` for a smaller diagnostic workload. This can reduce visual
accuracy. Fewer GPU layers may offload less work to the GPU; throughput must be measured.
Do not claim success from a zero-exit `doctor` result or from the synthetic technical demo.

For an already-running local server, the alternate TOML form is:

```toml
[runtime]
endpoint = "http://127.0.0.1:8081/v1"
model_revision = "your-pinned-model-and-runtime-revision"
context = 8192
max_tokens = 1536
request_timeout = 180
```

The CLI does not own or stop a server configured this way. The default managed form is preferred
when a single foreground command is desired. No hosted APIs or authentication keys are involved.
