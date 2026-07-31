# Publishing Workflow

## Create draft

```bash
python3 agents/agent-06-publisher.py submit --meta /tmp/smtinsider_article.meta.json
```

The publisher:

- preserves markdown line breaks;
- validates the target section;
- inserts `is_published=false`;
- stores `editorial_type` as `news`, `insight`, `review`, or `vendor`.

## List drafts

```bash
python3 agents/agent-06-publisher.py list
```

## Approve

```bash
python3 agents/agent-06-publisher.py approve --id 1234
```

Approve sets:

```sql
is_published = true
```

It does **not** clear `editorial_type`.

## Correct section manually

```sql
UPDATE news
SET editorial_type='review', category_name='X-Ray Inspection'
WHERE id=1234;
```

## Current example

```text
ID: 2895
URL: https://www.smtinsider.com/reviews/tri-tr7600-sv-series-axi-review
Section: review
Category: X-Ray Inspection
```


## Duplicate protection

Publisher blocks duplicates by default using `agents/dedupe.py`.

Override only when intentional:

```bash
python3 agents/agent-06-publisher.py submit --meta article.meta.json --allow-duplicate
```

See `DEDUPLICATION.md`.
