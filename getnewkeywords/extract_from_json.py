import json
import sys


def extract_keywords_from_json(json_file):
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        keywords = set()
        rcmd_list = data.get('rs', {}).get('rcmd', {}).get('list', [])
        
        if not rcmd_list:
            print("⚠️ No keywords in JSON")
            return keywords
        
        for item in rcmd_list:
            keywords.update(item.get('up', []))
            keywords.update(item.get('down', []))
        
        return keywords
        
    except FileNotFoundError:
        print(f"❌ File not found: {json_file}")
        return set()
    except json.JSONDecodeError:
        print(f"❌ Invalid JSON")
        return set()
    except Exception as e:
        print(f"❌ Error: {e}")
        return set()


def main():
    if len(sys.argv) < 2:
        print("=" * 70)
        print("USAGE: python extract_from_json.py response.json")
        print("=" * 70)
        print("\n1. Open https://m.baidu.com/ in browser")
        print("2. Search keyword")
        print("3. F12 > Network > find '/rec?' request")
        print("4. Copy response JSON and save to file")
        print("5. Run: python extract_from_json.py response.json")
        print("\n" + "=" * 70)
        sys.exit(1)
    
    json_file = sys.argv[1]
    output_file = "keywords_output.txt"
    
    print(f"📂 Reading: {json_file}")
    keywords = extract_keywords_from_json(json_file)
    
    if keywords:
        with open(output_file, 'w', encoding='utf-8') as f:
            for kw in sorted(keywords):
                f.write(kw + '\n')
        
        print(f"✅ Saved {len(keywords)} keywords to {output_file}")
        
        print("\n📋 Sample (first 10):")
        for kw in sorted(keywords)[:10]:
            print(f"  - {kw}")
        if len(keywords) > 10:
            print(f"  ... and {len(keywords) - 10} more")
    else:
        print("⚠️ No keywords extracted")


if __name__ == "__main__":
    main()
