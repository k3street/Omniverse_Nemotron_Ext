import os
import json
import glob

def parse_markdown_to_jsonl(docs_dir: str, output_file: str):
    """
    Simulates extracting chunks from Markdown documentation and formatting them 
    into an Instruction-Response format for fine-tuning.
    """
    print(f"Extracting docs from {docs_dir}...")
    dataset = []
    
    # In a real scenario, this would use a Markdown chunker (e.g., recursive character text splitter)
    # and use an LLM to generate synthetic Q&A pairs for each chunk.
    for md_file in glob.glob(f"{docs_dir}/*.md"):
        with open(md_file, "r", encoding="utf-8") as f:
            content = f.read()
            
            # Simulated naive chunking by H2 headers
            sections = content.split("\n## ")
            for section in sections[1:]:
                lines = section.split("\n")
                if len(lines) < 2:
                    continue
                
                title = lines[0].strip()
                body = "\n".join(lines[1:]).strip()
                
                if body:
                    dataset.append({
                        "instruction": f"Explain the concept of {title} in the NVIDIA Omniverse / Isaac Sim context.",
                        "input": "",
                        "output": body
                    })
                    
    with open(output_file, "w", encoding="utf-8") as out:
        for entry in dataset:
            out.write(json.dumps(entry) + "\n")
            
    print(f"Generated {len(dataset)} instructions in {output_file}")

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    # Target the extension Docs for testing purposes
    parse_markdown_to_jsonl("../../Docs", "data/doc_finetune_dataset.jsonl")
