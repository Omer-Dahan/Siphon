import json
import os
import time

JD_CONFIG_PATH = r"c:\Users\omer\AppData\Local\JDownloader 2\cfg\jd.controlling.linkcrawler.LinkCrawlerConfig.linkcrawlerrules.json"

def update_rules():
    if not os.path.exists(JD_CONFIG_PATH):
        print(f"File not found: {JD_CONFIG_PATH}")
        return

    try:
        with open(JD_CONFIG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                rules = []
            else:
                rules = json.loads(content)
        
        # Check if rule already exists
        rule_name = "Siphon Deep Scan (Level 2)"
        for r in rules:
            if r.get("name") == rule_name:
                print("Rule already exists. Updating...")
                r["maxDecryptDepth"] = 3
                r["enabled"] = True
                r["pattern"] = "https?://.+" 
                break
        else:
            # Create new rule
            new_rule = {
                "enabled": True,
                "logging": False,
                "maxDecryptDepth": 3, 
                "name": rule_name,
                "pattern": "https?://.+",
                "rule": "DEEPDECRYPT",
                "id": int(time.time() * 1000)
            }
            rules.append(new_rule)
            print(f"Added new rule: {rule_name}")

        # Write back
        with open(JD_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(rules, f, indent=4)
            
        print("Successfully updated JDownloader LinkCrawler Rules.")
        
    except Exception as e:
        print(f"Error updating rules: {e}")

if __name__ == "__main__":
    update_rules()
