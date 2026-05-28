import sys, re, html as html_module
sys.stdout.reconfigure(encoding='utf-8')

with open('E:/filter_keywords/hottrend/hottrain.html', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find 大家还在搜 section
idx = content.find('大家还在搜')
print(f'Found 大家还在搜 at index {idx}')

# Extract a large section for analysis
section = content[idx:idx+10000]

# Try to find text links within result items
# Looking for the keyword text inside the result items after 大家还在搜
# Based on the HTML structure seen, keywords appear in <a> tags with text content
# Let's find all spans with class "c-ellipsis" or similar
texts = re.findall(r'<span[^>]*class="[^"]*c-ellipsis[^"]*"[^>]*>([^<]+)</span>', section)
print('c-ellipsis texts:', texts[:30])

# Look for keyword links - typically in <a> tags
a_texts = re.findall(r'<a[^>]*data-rgitem-type="search"[^>]*>.*?<span[^>]*>([^<]+)</span>', section, re.DOTALL)
print('rgitem search texts:', a_texts[:20])

# Look for the "item" parameter in URLs
item_params = re.findall(r'%22item%22%3A%22([^%]+)%22', section)
decoded = [html_module.unescape(p) for p in item_params]
print('item params decoded:', decoded[:20])

# Look for keywords in &quot; encoded form
# relation_words contains all keywords separated by &amp;
rel_match = re.search(r'"relation_words"[^:]*:[^"]*"([^"]+)"', section)
if rel_match:
    relation_words = rel_match.group(1)
    # These are HTML encoded, unescape them
    relation_words = html_module.unescape(relation_words)
    keywords = [k.strip() for k in relation_words.split('&') if k.strip()]
    print('relation_words keywords:')
    for kw in keywords:
        print(f'  {kw}')
else:
    # Try &quot; encoded
    rel_match2 = re.search(r'relation_words&quot;:&quot;([^&]+(?:&amp;[^&]+)*)', section)
    if rel_match2:
        raw = rel_match2.group(1)
        raw = raw.replace('&amp;', '&').replace('&quot;', '"')
        keywords = [k.strip() for k in raw.split('&') if k.strip()]
        print('relation_words (alt) keywords:')
        for kw in keywords:
            print(f'  {kw}')
    else:
        print('relation_words not found')
        # Dump first 2000 chars
        print(section[:2000])
