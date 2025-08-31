import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import json
import re
import logging

# --- Configuration ---
# The starting URL for the website you want to scrape.
BASE_URL = "https://amucloud.in/"
# The maximum depth to crawl to prevent infinite loops or excessively long crawls.
MAX_DEPTH = 20
# Output file for the final JSON tree structure.
OUTPUT_FILENAME = "amucloud.json"

# --- Setup ---
# Set up logging to see the progress and any potential errors.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# A set to keep track of URLs we have already visited to avoid redundant requests and loops.
visited_urls = set()
# The main data structure to hold the nested tree of links.
link_tree = {}

def sanitize_key(key):
    """
    Sanitizes a string so it can be used as a clean key in the JSON tree.
    Removes leading/trailing whitespace and characters that are problematic in filenames.
    """
    # Remove any invalid file name characters and strip whitespace
    key = re.sub(r'[\\/*?:"<>|]', "", key)
    return key.strip()

def crawl_website(url, current_path_node, depth):
    """
    Recursively crawls a website to find Google Drive links and build a tree structure.

    Args:
        url (str): The URL of the page to crawl.
        current_path_node (dict): The current node in the link_tree dictionary to add sub-folders and links to.
        depth (int): The current crawl depth.
    """
    # Stop crawling if we've reached the max depth or have already visited this URL.
    if depth > MAX_DEPTH or url in visited_urls:
        return

    logging.info(f"Crawling (Depth {depth}): {url}")
    visited_urls.add(url)

    try:
        # Send a GET request to the URL with a timeout.
        response = requests.get(url, timeout=15)
        # Raise an exception for bad status codes (like 404 or 500).
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to fetch URL {url}: {e}")
        return

    # Parse the HTML content of the page.
    soup = BeautifulSoup(response.text, 'html.parser')

    # Find all anchor tags with an 'href' attribute.
    for link_tag in soup.find_all('a', href=True):
        # Get the clean text of the link, which represents the tile name.
        link_text = sanitize_key(link_tag.get_text(strip=True))
        # Resolve the relative URL to an absolute URL.
        absolute_url = urljoin(BASE_URL, link_tag['href'])

        # Skip empty links or javascript links
        if not link_text or absolute_url.startswith('javascript:'):
            continue

        # --- Logic to categorize and process links ---

        # 1. If it's a Google Drive link, add it as a "file" to the current node.
        if 'drive.google.com' in absolute_url:
            # Initialize the '_links' dictionary if it doesn't exist.
            if '_links' not in current_path_node:
                current_path_node['_links'] = {}
            # Add the link text and URL.
            current_path_node['_links'][link_text] = absolute_url
            logging.info(f"  -> Found Drive Link: '{link_text}'")

        # 2. If it's an internal link (on the same domain), treat it as a "folder" and crawl it.
        elif urlparse(absolute_url).netloc == urlparse(BASE_URL).netloc:
            # Create a new sub-folder in the tree if it's not already there.
            if link_text not in current_path_node:
                current_path_node[link_text] = {}
            # Recursively call the function for the new URL, going one level deeper.
            crawl_website(absolute_url, current_path_node[link_text], depth + 1)

def prune_empty_nodes(node):
    """
    Recursively removes empty nodes from the link tree.
    A node is considered empty if it doesn't contain a '_links' key and has no non-empty sub-nodes.
    Returns True if the node is empty after pruning, False otherwise.
    """
    # Iterate on a copy of keys since we might delete from the dictionary
    for key in list(node.keys()):
        if key == '_links':
            continue

        # If a sub-node is a dictionary, recurse into it
        if isinstance(node[key], dict):
            # If the recursive call returns True, it means the sub-node is empty
            if prune_empty_nodes(node[key]):
                # So we can delete it
                del node[key]

    # After pruning children, check if the current node is empty.
    # A node is empty if it doesn't have a '_links' key AND has no children left.
    if '_links' not in node and not any(isinstance(v, dict) for v in node.values()):
        return True  # It's empty
    return False # It's not empty

def main():
    """
    Main function to start the scraping process and save the results.
    """
    print("Starting website crawl to find Google Drive links...")
    print(f"Base URL: {BASE_URL}")
    print("-" * 30)

    # Start the crawl from the base URL.
    crawl_website(BASE_URL, link_tree, depth=0)

    # After crawling, prune the tree to remove empty directories.
    print("\nCrawling finished. Pruning empty directories...")
    prune_empty_nodes(link_tree)


    # Save the completed tree structure to a JSON file.
    try:
        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(link_tree, f, indent=4, ensure_ascii=False)
        print("-" * 30)
        print(f"✅ Pruning complete! The final link tree has been saved to '{OUTPUT_FILENAME}'")
    except IOError as e:
        print(f"❌ Error saving file: {e}")


if __name__ == "__main__":
    main()

