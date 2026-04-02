#!/bin/bash
set -e

echo "Exporting LoRA adapters to GGUF format..."

# This script assumes you have llama.cpp cloned or unsloth natively handles GGUF export.
# In Unsloth, you can just add `model.save_pretrained_gguf("model", tokenizer, quantization_method = "q4_k_m")`
# However, if doing it manually with llama.cpp:

if [ ! -d "llama.cpp" ]; then
    echo "Cloning llama.cpp to handle standard GGUF conversion if needed..."
    git clone https://github.com/ggerganov/llama.cpp
    cd llama.cpp
    make
    cd ..
    python3 -m pip install -r llama.cpp/requirements.txt
fi

echo "Converting model to GGUF..."
# If using Unsloth, we would run a short python script:
cat << 'EOF' > convert_unsloth_gguf.py
from unsloth import FastLanguageModel
import torch

MODEL_NAME = "lora_model" # Path to saved unsloth model

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name = MODEL_NAME,
    max_seq_length = 4096,
    dtype = None,
    load_in_4bit = True,
)

print("Exporting to GGUF (Q4_K_M)...")
model.save_pretrained_gguf("tuned_nemotron", tokenizer, quantization_method = "q4_k_m")
print("Done!")
EOF

python3 convert_unsloth_gguf.py

echo "GGUF export finished."
echo "You can now use the tuned_nemotron-unsloth.Q4_K_M.gguf file with Ollama."
