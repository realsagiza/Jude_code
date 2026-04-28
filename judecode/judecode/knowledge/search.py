"""Search & Knowledge Graph for the Vault."""

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from judecode.knowledge.vault import get_vault_path


def search_vault(query: str) -> list[dict]:
    """Search notes by title, content, or tag."""
    vault = get_vault_path()
    results = []
    query_lower = query.lower()
    
    for md_file in vault.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        content_lower = content.lower()
        
        score = 0
        if query_lower in content_lower:
            score = content_lower.count(query_lower)
        
        # Also search in filename
        rel = str(md_file.relative_to(vault))
        if query_lower in rel.lower():
            score += 5
            
        # Tag search
        tags = re.findall(r'#(\w[\w-]*)', content)
        if query_lower in [t.lower() for t in tags]:
            score += 10
        
        if score > 0:
            # Extract snippet around match
            snippet = ""
            idx = content_lower.find(query_lower)
            if idx >= 0:
                start = max(0, idx - 80)
                end = min(len(content), idx + 120)
                snippet = content[start:end].replace("\n", " ")
            
            results.append({
                "title": str(md_file.relative_to(vault).with_suffix("")),
                "score": score,
                "snippet": snippet,
                "tags": tags,
            })
    
    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:20]  # Top 20


def build_knowledge_graph() -> dict[str, Any]:
    """Build a graph of all notes, their links, and tags."""
    vault = get_vault_path()
    nodes = []
    edges = []
    tag_map = defaultdict(list)
    
    for md_file in vault.rglob("*.md"):
        rel = str(md_file.relative_to(vault).with_suffix(""))
        content = md_file.read_text(encoding="utf-8")
        
        tags = re.findall(r'#(\w[\w-]*)', content)
        links = re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', content)
        
        nodes.append({
            "id": rel,
            "title": rel,
            "tags": tags,
            "link_count": len(links),
        })
        
        for tag in tags:
            tag_map[tag].append(rel)
        
        for link in links:
            link_clean = link.strip()
            edges.append({
                "source": rel,
                "target": link_clean,
                "type": "link",
            })
    
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "tags": dict(tag_map),
    }


def get_related_notes(title: str) -> list[str]:
    """Find notes related to a given note (by shared tags or direct links)."""
    vault = get_vault_path()
    target_path = vault / f"{title}.md"
    if not target_path.exists():
        # Try to find by slug
        for f in vault.rglob("*.md"):
            if f.stem.lower() == title.lower().replace(" ", "-"):
                target_path = f
                break
    
    if not target_path.exists():
        return []
    
    content = target_path.read_text(encoding="utf-8")
    target_tags = set(re.findall(r'#(\w[\w-]*)', content))
    target_links = set(re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', content))
    
    related = []
    for md_file in vault.rglob("*.md"):
        if md_file == target_path:
            continue
        
        other_content = md_file.read_text(encoding="utf-8")
        other_tags = set(re.findall(r'#(\w[\w-]*)', other_content))
        other_links = set(re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', other_content))
        
        shared_tags = target_tags & other_tags
        mutual_links = (target_links & other_links) or (f"[[{title}]]" in other_content)
        
        if shared_tags or mutual_links:
            rel = str(md_file.relative_to(vault).with_suffix(""))
            related.append({
                "title": rel,
                "shared_tags": list(shared_tags),
                "linked": mutual_links,
            })
    
    return related


def get_notes_by_tag(tag: str) -> list[str]:
    """Get all notes that have a specific tag."""
    vault = get_vault_path()
    results = []
    for md_file in vault.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        tags = re.findall(r'#(\w[\w-]*)', content)
        if tag.lower() in [t.lower() for t in tags]:
            results.append(str(md_file.relative_to(vault).with_suffix("")))
    return results
