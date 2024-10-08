import os
import re
import requests
import yaml
import argparse
import html2text
from datetime import datetime
from tqdm import tqdm

# Output directory for Markdown files
CONTENT_DIR = "content"

# Ensure content directory exists
if not os.path.exists(CONTENT_DIR):
    os.makedirs(CONTENT_DIR)

# Initialize html2text converter
html_converter = html2text.HTML2Text()
html_converter.ignore_images = False  # Allow image links
html_converter.ignore_links = False   # Allow hyperlinks
html_converter.body_width = 0         # Preserve line breaks in Markdown
html_converter.single_line_break = True  # Handle single line breaks better
html_converter.protect_links = True   # Prevent links from being modified
html_converter.ignore_emphasis = False  # Keep bold/italic
html_converter.ignore_tables = False  # Allow table HTML
html_converter.bypass_tables = False  # Keep tables as HTML

def fetch_wordpress_data(domain_url, endpoint, per_page=100):
    """Fetch paginated data from the WordPress REST API."""
    data = []
    page = 1

    while True:
        url = f"{domain_url}/wp-json/wp/v2/{endpoint}?per_page={per_page}&page={page}&_embed"
        response = requests.get(url)

        # Check if the response is empty or not a valid JSON response
        try:
            response.raise_for_status()
            items = response.json()

            if not items:  # No data returned
                tqdm.write(f"No data found at {url}")
                break

            data.extend(items)
            
            # If fewer items than `per_page` are returned, we reached the end
            if len(items) < per_page:
                break

        except requests.exceptions.HTTPError as http_err:
            tqdm.write(f"HTTP error occurred: {http_err} - {url}")
            break
        except requests.exceptions.JSONDecodeError as json_err:
            tqdm.write(f"JSON decode error: {json_err} - {url}")
            break
        except Exception as err:
            tqdm.write(f"Other error occurred: {err} - {url}")
            break

        page += 1

    return data

def map_term_ids_to_names(ids, terms):
    """Map a list of term IDs to their names."""
    return [terms.get(term_id, {'id': term_id, 'name': f"Unknown (ID: {term_id})"})['name'] for term_id in ids]

def save_as_markdown(file_path, frontmatter, content):
    """Save data as a Markdown file with YAML frontmatter."""
    # Ensure title is properly extracted and sanitized
    title = frontmatter.get('title', 'Untitled')
    if isinstance(title, dict):
        title = title.get('rendered', 'Untitled')
    frontmatter['title'] = title

    # Save frontmatter and content as a valid Markdown file
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        yaml.dump(frontmatter, f, allow_unicode=True, sort_keys=False)
        f.write("---\n\n")
        f.write(content)

def process_media_links(content, media_base_url):
    """Replace external media links with local versions."""
    content = re.sub(r'!\[(.*?)\]\((https://example.com/wp-content/uploads/(.*?)\))', r'!\[\1\](\3)', content)
    return content

def convert_post_to_md(post, authors, categories, tags, custom_taxonomies, post_type="post", use_markdown=True):
    """Convert WordPress post or page to Markdown with HTML preservation for complex blocks."""
    slug = post.get('slug', f'{post_type}-{post.get("id")}')  # Use slug or fallback to id
    slug = slug.replace('/', '-')  # Ensure no slashes in filenames
    
    # Get the raw HTML content
    html_content = post.get('content', {}).get('rendered', '')

    # Split content by Gutenberg blocks or custom elements
    # Detect Gutenberg blocks (div.wp-block-* or any element containing class="wp-block-*")
    blocks = re.split(r'(<div[^>]*wp-block[^>]*>.*?</div>)', html_content, flags=re.DOTALL)

    # Process each block, converting simple elements to Markdown and leaving complex HTML intact
    converted_content = ""
    for block in blocks:
        # If the block is a Gutenberg block or custom HTML, keep it as raw HTML
        if re.search(r'wp-block', block):
            converted_content += block.strip() + "\n\n"  # Add raw HTML block
        else:
            # Convert simple HTML to Markdown using html2text for non-Gutenberg content
            if use_markdown:
                converted_content += html_converter.handle(block).strip() + "\n\n"
            else:
                converted_content += block.strip() + "\n\n"

    # Replace media URLs in the Markdown content
    content = process_media_links(converted_content, domain_url)

    # Get author name using author ID
    author_id = post.get('author', 0)
    author_name = authors.get(author_id, "Unknown")

    # Extract the title properly from the rendered field
    title = post.get('title', {}).get('rendered', 'Untitled')

    # Define the most important frontmatter elements first
    frontmatter = {
        'title': title,
        'date': post.get('date', ''),
        'author': author_name,
        'excerpt': html_converter.handle(post.get('excerpt', {}).get('rendered', '')).strip(),
        'custom_url': post.get('slug', ''),
        'type': post_type  # 'post' or 'page'
    }

    # Map categories and tags by ID to their names
    frontmatter['categories'] = map_term_ids_to_names(post.get('categories', []), categories)
    frontmatter['tags'] = map_term_ids_to_names(post.get('tags', []), tags)

    # Add ACF data if available
    acf_data = post.get('acf', None)
    if acf_data:
        frontmatter['acf'] = acf_data

    # Add custom taxonomies
    for taxonomy, terms in custom_taxonomies.items():
        if taxonomy in post:
            frontmatter[taxonomy] = map_term_ids_to_names(post.get(taxonomy, []), terms)

    # Add any other remaining metadata, excluding unnecessary fields
    filtered_metadata = {k: v for k, v in post.items() if k not in ['content', 'excerpt', 'guid', '_links', '_embedded', 'acf']}
    frontmatter.update(filtered_metadata)

    # Define the file path based on the post type and slug
    file_dir = os.path.join(CONTENT_DIR, f"{post_type}s")
    os.makedirs(file_dir, exist_ok=True)

    file_path = os.path.join(file_dir, f"{slug}.md")

    # Save the post content as a markdown file
    save_as_markdown(file_path, frontmatter, content)
    tqdm.write(f"Saved {post_type}: {file_path}")

def fetch_terms_by_taxonomy(domain_url, taxonomy):
    """Fetch terms for a specific taxonomy (e.g., categories, tags, custom taxonomies)."""
    terms = fetch_wordpress_data(domain_url, taxonomy)
    # Ensure the term data has both 'id' and 'name' keys for each term
    return {term['id']: {'id': term['id'], 'name': term['name']} for term in terms}

def fetch_custom_taxonomies(domain_url):
    """Fetch all available custom taxonomies from the WordPress REST API."""
    url = f"{domain_url}/wp-json/wp/v2/taxonomies"
    response = requests.get(url)

    try:
        response.raise_for_status()
        taxonomies = response.json()

        # Filter custom taxonomies by checking if they are not "category" or "post_tag"
        custom_taxonomies = {key: val for key, val in taxonomies.items() if key not in ['category', 'post_tag']}
        
        # Now fetch terms for each custom taxonomy
        taxonomy_terms = {}
        for taxonomy in custom_taxonomies.keys():
            terms_url = f"{domain_url}/wp-json/wp/v2/{taxonomy}?per_page=100"
            try:
                taxonomy_terms[taxonomy] = fetch_terms_by_taxonomy(domain_url, taxonomy)
            except requests.exceptions.HTTPError as http_err:
                tqdm.write(f"HTTP error occurred: {http_err} - {terms_url}")
                continue  # Skip this taxonomy if 404 or any other error occurs

        return taxonomy_terms

    except requests.exceptions.HTTPError as http_err:
        tqdm.write(f"HTTP error occurred while fetching taxonomies: {http_err}")
    except requests.exceptions.JSONDecodeError as json_err:
        tqdm.write(f"JSON decode error while fetching taxonomies: {json_err}")
    except Exception as err:
        tqdm.write(f"Other error occurred while fetching taxonomies: {err}")

    return {}

def save_posts_and_pages(domain_url, authors, categories, tags, custom_taxonomies, use_markdown):
    """Fetch and save all posts and pages as markdown files."""
    print("Fetching all posts...")
    posts = fetch_wordpress_data(domain_url, "posts")

    print(f"Total posts fetched: {len(posts)}")

    print("Fetching all pages...")
    pages = fetch_wordpress_data(domain_url, "pages")

    print(f"Total pages fetched: {len(pages)}")

    # Start the progress bar for both posts and pages
    total_items = len(posts) + len(pages)
    with tqdm(total=total_items, desc="Converting to Markdown", unit="item") as pbar:
        for post in posts:
            convert_post_to_md(post, authors, categories, tags, custom_taxonomies, post_type="post", use_markdown=use_markdown)
            pbar.update(1)
        for page in pages:
            convert_post_to_md(page, authors, categories, tags, custom_taxonomies, post_type="page", use_markdown=use_markdown)
            pbar.update(1)

def save_authors(domain_url):
    """Fetch and save all authors as markdown metadata."""
    print("Fetching authors...")
    authors = fetch_wordpress_data(domain_url, "users")
    print(f"Total authors fetched: {len(authors)}")

    # Save authors as a YAML metadata file
    authors_path = os.path.join(CONTENT_DIR, "authors.yml")
    authors_dict = {author['id']: author['name'] for author in authors}

    with open(authors_path, "w") as f:
        yaml.dump(authors_dict, f, allow_unicode=True)
    print(f"Saved authors metadata: {authors_path}")

    return authors_dict

def save_categories_and_tags(domain_url):
    """Fetch and save all categories and tags as markdown metadata."""
    print("Fetching categories...")
    categories = fetch_terms_by_taxonomy(domain_url, "categories")
    print(f"Total categories fetched: {len(categories)}")

    print("Fetching tags...")
    try:
        tags = fetch_terms_by_taxonomy(domain_url, "tags")
    except requests.exceptions.JSONDecodeError:
        tqdm.write(f"Tags API did not return valid JSON; continuing without tags.")
        tags = {}

    print(f"Total tags fetched: {len(tags)}")

    # Save categories as a YAML metadata file
    categories_path = os.path.join(CONTENT_DIR, "categories.yml")
    with open(categories_path, "w") as f:
        yaml.dump(categories, f, allow_unicode=True)
    tqdm.write(f"Saved categories metadata: {categories_path}")

    # Save tags as a YAML metadata file
    tags_path = os.path.join(CONTENT_DIR, "tags.yml")
    with open(tags_path, "w") as f:
        yaml.dump(tags, f, allow_unicode=True)
    tqdm.write(f"Saved tags metadata: {tags_path}")

    return categories, tags

if __name__ == "__main__":
    # Track start time
    start_time = datetime.now()

    # Use argparse to require domain URL as input
    parser = argparse.ArgumentParser(description="Fetch WordPress data and convert to Markdown")
    parser.add_argument('domain', type=str, help="Your WordPress site URL (e.g., https://your-site.com)")
    parser.add_argument('--markdown', action='store_true', help="Convert HTML content to Markdown")

    args = parser.parse_args()
    domain_url = args.domain.rstrip("/")  # Ensure no trailing slash
    use_markdown = args.markdown  # Check if markdown flag is set

    # Fetch and save authors, custom taxonomies, posts, pages, categories, and tags
    authors = save_authors(domain_url)
    categories, tags = save_categories_and_tags(domain_url)
    custom_taxonomies = fetch_custom_taxonomies(domain_url)
    save_posts_and_pages(domain_url, authors, categories, tags, custom_taxonomies, use_markdown=use_markdown)

    # Calculate and display total time taken
    total_time = datetime.now() - start_time
    print(f"Total time taken: {total_time.total_seconds()} seconds")
