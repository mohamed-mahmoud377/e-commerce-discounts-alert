"""Turn docs/queries.sql into Datasette canned queries (one clickable page per query).

Usage: python3 scripts/datasette_metadata.py docs/queries.sql > metadata.json
"""

import json
import re
import sys

text = open(sys.argv[1]).read()
queries = {}
# each query starts with a comment line like "-- 7. Biggest drops in the last 3 days"
for match in re.finditer(r"^-- (\d+)\. ([^\n]+)\n(.*?)(?=^-- \d+\. |\Z)", text, re.S | re.M):
    number, title, body = match.groups()
    sql = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("--")).strip().rstrip(";")
    queries[f"q{int(number):02d}"] = {"title": f"{number}. {title}", "sql": sql}

print(json.dumps({
    "title": "deals: live server database (read-only)",
    "description": "Prices are in EGP in the queries (raw tables store piastres). Times are UTC.",
    "databases": {"deals": {"queries": queries}},
}, indent=1))
