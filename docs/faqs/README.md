# Authoring FAQ Entries

This folder holds the individual Frequently Asked Questions (FAQs) for the STARR-OMOP documentation site. Each question is a small standalone Quarto file. They are compiled into a single FAQ page automatically — you never edit the final page directly.

## How Generation Works

The [scripts/generate_faq.py](../../scripts/generate_faq.py) script:

1. Globs every file matching `docs/faqs/q*.qmd` (sorted alphabetically — this determines display order).
2. Reads each file's YAML frontmatter and uses the `description` value as the **question header**.
3. Wraps the body of each file in a collapsible callout (`::: {.callout-note collapse="true"}`).
4. Writes the combined result to `docs/faq.qmd`.

`docs/faq.qmd` is a **generated file** — do not edit it by hand; your changes will be overwritten.

This script runs automatically as a Quarto `pre-render` hook (defined in [docs/_quarto.yml](../_quarto.yml)), so `quarto preview`, `quarto render`, and `quarto publish` all regenerate the FAQ page. You can also run it manually:

```bash
source ../../.venv/bin/activate
python ../../scripts/generate_faq.py
```

### Important: file naming

- **Only files whose name starts with `q` are collected** (the `q*.qmd` glob).
- [faq_template.qmd](faq_template.qmd) does **not** start with `q`, so it is ignored — it exists purely as a starting point.
- Files are sorted by name, so use a consistent scheme (for example `q1.qmd`, `q2.qmd`, or a descriptive `q_concept_lookup.qmd`) to control ordering.

## Adding a New Question

1. Copy the template to a new `q*.qmd` file:

   ```bash
   cp faq_template.qmd q<next-number>.qmd
   ```

2. Edit the frontmatter. The `description` becomes the collapsible question header shown on the FAQ page:

   ```yaml
   ---
   description: "How do I look up the human-readable name for a concept ID?"
   code-annotations: hover
   ---
   ```

3. Fill in the body sections (see [q1.qmd](q1.qmd) for a complete worked example):
   - **Question** — the full question, phrased the way a user would ask it.
   - **Explanation** — step-by-step context and SQL/Python. Break the analysis into small logical steps rather than one large block.
   - **Conclusion** — a short wrap-up of the answer.

4. Preview to verify it renders and (if it contains code) executes:

   ```bash
   cd .. && quarto preview
   ```

## Writing Code Cells

FAQ entries can include executable Python code that runs **at render time**. This means:

- You must be authenticated with `gcloud` (see the root [README](../../README.md)) — cells that query BigQuery will actually run.
- Use a suitable project for queries (for example the training project `som-rit-starr-training`, as in [q1.qmd](q1.qmd)).
- Keep queries small (use `LIMIT`) so the site renders quickly.

Use Quarto [code annotations](https://quarto.org/docs/authoring/code-annotation.html) (the `# <1>`, `# <2>` markers with a numbered list below the block) to explain code line by line instead of cluttering it with inline comments. Set `code-annotations: hover` in the frontmatter, as the template does.

To render a result table without showing the query-execution boilerplate, hide the input with cell options:

````markdown
```{python}
#| echo: false
#| warning: false
#| message: false

from IPython.display import Markdown
Markdown(df.to_markdown(index=False))
```
````

## What Happens When You Add a Question

1. You add a new `q*.qmd` file here.
2. On the next `quarto preview` / `render` / `publish`, `generate_faq.py` runs and rebuilds `docs/faq.qmd`, adding your entry as a new collapsible section (ordered by filename).
3. Any code cells execute against BigQuery and their output is embedded in the page.
4. After merging to `main`, running `quarto publish gh-pages` (from `docs/`) publishes the updated FAQ page to the live site.
