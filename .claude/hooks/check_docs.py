"""
Claude Code Stop Hook: 检测代码变更后是否需要更新文档。
由 .claude/settings.local.json 中的 Stop hook 触发。
"""
import os, sys, json, hashlib, subprocess, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PROJECT = r'C:\Users\董文涛\PycharmProjects\bidding-ecp-data'
DOC_LINKS = os.path.join(PROJECT, '.doc-links.yaml')
STATE = os.path.join(PROJECT, '.claude', '.doc_state.json')

# ============================================================
# 1. 加载文档-代码映射
# ============================================================
if not os.path.exists(DOC_LINKS):
    print("[doc-hook] .doc-links.yaml 不存在, 跳过")
    sys.exit(0)

import yaml
try:
    with open(DOC_LINKS, 'r', encoding='utf-8') as f:
        links = yaml.safe_load(f) or []
except Exception:
    print("[doc-hook] .doc-links.yaml 解析失败, 跳过")
    sys.exit(0)

# ============================================================
# 2. 获取自上次检查以来的代码变更
# ============================================================
last_state = {}
if os.path.exists(STATE):
    with open(STATE, 'r', encoding='utf-8') as f:
        last_state = json.load(f)

# 计算当前代码文件的哈希
current_hashes = {}
for entry in links:
    for code_file in entry.get('codes', []):
        path = os.path.join(PROJECT, code_file)
        if os.path.exists(path):
            with open(path, 'rb') as f:
                current_hashes[code_file] = hashlib.sha256(f.read()).hexdigest()

# 找出变更的文件
changed_files = []
for code_file, h in current_hashes.items():
    if last_state.get(code_file) != h:
        changed_files.append(code_file)

# 找出受影响的文档
stale_docs = set()
for entry in links:
    for code_file in entry.get('codes', []):
        if code_file in changed_files:
            stale_docs.add(entry.get('doc', 'unknown'))

# ============================================================
# 3. 输出结果, 保存状态
# ============================================================
if stale_docs:
    print(f"\n{'='*60}")
    print(f"📄 [doc-hook] 检测到 {len(changed_files)} 个代码文件变更")
    print(f"   受影响文档: {len(stale_docs)} 篇")
    print(f"{'='*60}")
    print("变更代码文件:")
    for cf in changed_files:
        print(f"  • {cf}")
    print("\n可能需要更新的文档:")
    for doc in sorted(stale_docs):
        doc_path = os.path.join(PROJECT, doc) if not os.path.isabs(doc) else doc
        print(f"  📝 {doc}")
    print("\n💡 在下次对话中请检查并更新这些文档。")
    print(f"{'='*60}\n")
else:
    print(f"[doc-hook] ✅ 文档与代码一致 (监控 {len(current_hashes)} 个文件)")

# 保存当前状态
with open(STATE, 'w', encoding='utf-8') as f:
    json.dump(current_hashes, f, ensure_ascii=False, indent=2)
