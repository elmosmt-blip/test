# Troubleshooting

## Text renders as one giant heading

Cause: markdown line breaks were collapsed before publishing.

Fix implemented in:

```text
agents/agent-06-publisher.py -> html_to_plain()
```

It now preserves paragraph breaks and markdown headings.

## Article appears in wrong section

Check:

```sql
SELECT id, title, editorial_type, slug FROM news WHERE id=...;
```

Valid values:

```text
news / insight / review / vendor
```

Use `agents/section_router.py` to test routing.

## Fresh collector finds no DuckDuckGo results

DuckDuckGo may challenge bots. This is expected.

The collector uses RSS fallback and should still collect dated fresh signals.

## Do not accidentally write to DB

Keep:

```env
ALLOW_DB_WRITES=0
```

Enable only for real draft creation or approve/delete.

## Real LLM not configured

If `LLM_MOCK=1`, local mock behavior is used. For production:

```env
LLM_MOCK=0
LLM_API_BASE=...
LLM_API_KEY=...
LLM_MODEL=...
```


## Duplicate publication blocked

This is expected when the source URL/title already exists on SMTInsider.

Check:

```text
DEDUPLICATION.md
```

Override only if intentional:

```bash
python3 agents/agent-06-publisher.py submit --meta article.meta.json --allow-duplicate
```
