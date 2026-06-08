#!/usr/bin/env python3
"""
Generate FAQ page from individual FAQ files.
This script collects all FAQ entries from faqs/q*.qmd files and generates a comprehensive faq.qmd file.
"""

import glob
import yaml
from pathlib import Path
import os

# Change to the docs directory
docs_dir = Path(__file__).parent.parent / "docs"
os.chdir(docs_dir)

# Get all q*.qmd files in the faqs folder
faq_files = sorted(glob.glob("faqs/q*.qmd"))

# Start building the FAQ content
faq_content = """---
title: "FAQ"
description: "Frequently Asked Questions for STARR-OMOP"
code-annotations: hover
---

Below a list of frequently asked questions for STARR-OMOP v5.4.

"""

# Add each FAQ as a collapsible section
for faq_file_path in faq_files:
    # Read the file content
    with open(faq_file_path, "r") as f:
        content = f.read()

    # Extract description from YAML frontmatter and get content without frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1])
            description = frontmatter.get("description", "Question")
            # Get content after the frontmatter
            main_content = parts[2].strip()
        else:
            description = "Question"
            main_content = content
    else:
        description = "Question"
        main_content = content

    # Add the collapsible callout block with the actual content
    faq_content += '::: {.callout-note collapse="true"}\n'
    faq_content += f"## {description}\n\n"
    faq_content += main_content + "\n\n"
    faq_content += ":::\n\n"

# Write the generated content to faq.qmd
with open("faq.qmd", "w") as f:
    f.write(faq_content)

print(f"Generated faq.qmd from {len(faq_files)} FAQ files")
