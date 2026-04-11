#!/usr/bin/env python3
import sys
import os
import urllib.request
import urllib.error
import urllib.parse
from html.parser import HTMLParser
import time

# Ensure we can import service
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, root_dir)

from service.isaac_assist_service.knowledge.knowledge_base import KnowledgeBase
from service.isaac_assist_service.config import config

class DocParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_content = False
        self.text_chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in ["p", "div", "li", "a", "h1", "h2", "h3"]:
            self.in_content = True

    def handle_data(self, data):
        data = data.strip()
        if self.in_content and data:
            self.text_chunks.append(data)

    def get_text(self):
        return " \n".join(self.text_chunks)

def scrape_docs(test_mode=False):
    # Force opt-in just for the automated extraction
    config.contribute_data = True 
    kb = KnowledgeBase(storage_dir=os.path.join(root_dir, "workspace", "knowledge"))
    
    url = "https://docs.isaacsim.omniverse.nvidia.com/5.1.0/py/index.html"
    print(f"Scraping Isaac Sim Documentation: {url}")
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) OmniverseAssistantScraper'})
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            
        parser = DocParser()
        parser.feed(html)
        content = parser.get_text()
        
        # In test mode we limit to top 1500 chars to avoid blowing up JSONL with garbage
        preview = content[:2500] + "...\n[CONTINUED_IN_INDEX]"
        
        instruction = "Provide a high-level summary of the Isaac Sim 5.1.0 Python API architecture."
        
        success = kb.add_entry(
            version="5.1.0",
            instruction=instruction,
            response=preview,
            source="nvidia_docs_scraper"
        )
        
        if success:
            print("[\u2713] Successfully extracted top-level documentation and committed to Knowledge Base 5.1.0.")
            if test_mode:
                print(f"Preview snippet:\n{preview[:300]}...")
        else:
            print("[X] Failed to insert into knowledge base.")
            
    except urllib.error.URLError as e:
        print(f"[X] Network/Scrape Error: {e.reason}")
    except Exception as e:
        print(f"[X] Error: {e}")

if __name__ == "__main__":
    test_mode = "--test-mode" in sys.argv
    scrape_docs(test_mode=test_mode)
