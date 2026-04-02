\"\"\"
Fine-Tuning Nemotron via Unsloth (QLoRA)
This script loads a base model (e.g. nemotron-cascade-2 base / llama-3 architecture),
attaches LoRA adapters, trains on the generated jsonl dataset, and saves the adapter.
\"\"\"
import os
import torch
# Make sure to install unsloth, transformers, trl, peft
from unsloth import FastLanguageModel
from transformers import TrainingArguments
from trl import SFTTrainer
from datasets import load_dataset

# Configuration
MODEL_NAME = "nvidia/Nemotron-4-340B-Instruct"  # Replace with actual HF base model corresponding to local Ollama model if possible, or standard Llama-3 8B if cascaded.
MAX_SEQ_LENGTH = 4096
DTYPE = None # None for auto detection. Float16 for Tesla T4, Bfloat16 for Ampere+
LOAD_IN_4BIT = True

def format_prompt(examples):
    instructions = examples["instruction"]
    inputs       = examples["input"]
    outputs      = examples["output"]
    texts = []
    for instruction, input_text, output in zip(instructions, inputs, outputs):
        text = f"<|im_start|>user\n{instruction}\n{input_text}<|im_end|>\n<|im_start|>assistant\n{output}<|im_end|>"
        texts.append(text)
    return { "text" : texts }

def main():
    print("Loading model and tokenizer via Unsloth...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name = MODEL_NAME,
        max_seq_length = MAX_SEQ_LENGTH,
        dtype = DTYPE,
        load_in_4bit = LOAD_IN_4BIT,
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r = 16,
        target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
                          "gate_proj", "up_proj", "down_proj",],
        lora_alpha = 16,
        lora_dropout = 0, 
        bias = "none",
        use_gradient_checkpointing = "unsloth",
        random_state = 3407,
        use_rslora = False,
        loftq_config = None,
    )

    print("Loading and preparing dataset...")
    # Load dataset generated from Phase 1
    dataset = load_dataset("json", data_files={"train": "../data_curation/data/code_finetune_dataset.jsonl"}, split="train")
    dataset = dataset.map(format_prompt, batched = True)

    print("Setting up Trainer...")
    trainer = SFTTrainer(
        model = model,
        tokenizer = tokenizer,
        train_dataset = dataset,
        dataset_text_field = "text",
        max_seq_length = MAX_SEQ_LENGTH,
        dataset_num_proc = 2,
        packing = False, # Can make training 5x faster for short sequences.
        args = TrainingArguments(
            per_device_train_batch_size = 2,
            gradient_accumulation_steps = 4,
            warmup_steps = 5,
            max_steps = 60,
            learning_rate = 2e-4,
            fp16 = not torch.cuda.is_bf16_supported(),
            bf16 = torch.cuda.is_bf16_supported(),
            logging_steps = 1,
            optim = "adamw_8bit",
            weight_decay = 0.01,
            lr_scheduler_type = "linear",
            seed = 3407,
            output_dir = "outputs",
        ),
    )

    print("Starting Training...")
    trainer_stats = trainer.train()

    print("Saving LoRA model...")
    model.save_pretrained("lora_model") 
    tokenizer.save_pretrained("lora_model")
    print("Training complete! Model saved to lora_model/")

if __name__ == "__main__":
    main()
