"""Everything that must stay fixed for a reproducible trial. Values come from baseline.json."""

UPSTREAM_REPO = "https://github.com/moruku36/qwen-multimodal-colab"
UPSTREAM_SHA = "9e5ff82cf604dd3b0377de81b89e1f4aaa422947"
UPSTREAM_NOTEBOOK_BLOB = "5e40a89f0253356c9c8d72698a93508f4d30d8aa"
LLAMA_CPP_COMMIT = "4da6337767f973e2b4d0797e5b323d77d8565e4a"

# Hugging Face revisions are the commits that last changed each file (baseline.json). Downloads
# use these revisions and the bytes are checked against SHA256 before anything loads them.
MODELS = {
    "chat": {
        "repo": "huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF",
        "file": "Huihui-Qwen3.8-27B-abliterated-UD-DW-Q8_K_L.gguf",
        "revision": "ff733b88376282017b7f4675d6d96536c6ffa712",
        "sha256": "fb8413d0b5cec5ad7055e49630a986518ba90fdacc95a337aa535e8ff5bf0d16",
        "size_bytes": None,  # publisher shows "27.3 GB" only; the hash is the check
    },
    "mmproj": {
        "repo": "ggml-org/Qwen3.8-27B-GGUF",
        "file": "mmproj-Qwen3.8-27B-Q8_0.gguf",
        "revision": "97c30c65c8d9a3e73f9fdfb50f1d1a669e9a2827",
        "sha256": "2e968a6af97ce35d8971890b257b9b7edabf20ad91450501fa53162a19ee33eb",
        "size_bytes": 629247008,
    },
}

GRADIO_PORT = 7860
LLAMA_HOST = "127.0.0.1"
LLAMA_PORT = 8012
