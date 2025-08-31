import os
import re
import json

# ==== CONFIGURATION ====
REPO_DIR = "amupedia-web"  # <--- CHANGE THIS to your repo path!

drive_regex = re.compile(r"https://drive\.google\.com/[^\s\"'\),]+")
CODE_EXTS = (".js", ".jsx", ".ts", ".tsx")

def is_code_file(filename):
    return filename.endswith(CODE_EXTS)

def get_links_from_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            contents = f.read()
            return drive_regex.findall(contents)
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return []

def insert_links(tree, relpath_parts, links):
    node = tree
    for part in relpath_parts[:-1]:  # traverse dirs
        node = node.setdefault(part, {})
    file_node = node.setdefault(relpath_parts[-1], {})
    if links:
        file_node.setdefault("_links", []).extend(links)

def main():
    tree = {}
    for root, dirs, files in os.walk(REPO_DIR):
        for file in files:
            if is_code_file(file):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, REPO_DIR)
                path_parts = rel_path.split(os.sep)
                links = get_links_from_file(full_path)
                if links:
                    insert_links(tree, path_parts, links)
    output_file = "amupedia_drive_links_full_branch_tree.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2, ensure_ascii=False)
    print(f"\n✅ All Google Drive links found and tree written to {output_file}")

if __name__ == "__main__":
    main()