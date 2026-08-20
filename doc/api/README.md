# REST API documentation

This folder holds the generated OpenAPI specification of the UDS REST API
(`rest.json`, `rest.yaml`) and the browsable HTML rendering (`rest.html`).

All three files are **generated**; do not edit them by hand. Regenerate them
whenever a REST handler, a model serializer or a GUI field description changes,
and include the result in the same pull request as the change that caused it.

## 1. Regenerate the specification

From `src`, with the virtualenv active:

```bash
python manage.py genapi -o ../doc/api/rest
```

The command walks the REST dispatcher tree and writes both `rest.json` and
`rest.yaml`. Use `-f json` or `-f yaml` to emit only one of them.

## 2. Regenerate the HTML

From this folder (`doc/api`):

```bash
npx @redocly/cli build-docs rest.yaml -o rest.html
```

This produces a single self-contained `rest.html` from the YAML spec, so run it
after step 1.
