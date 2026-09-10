# Third-party notices — Onside experiment

## Onside Arena

This experiment consumes public football-model output published by Onside Arena at https://onsidearena.com/.

Attribution used by this project: **Onside — https://onsidearena.com/**.

Onside states that its graded Fantasy Premier League predictions dataset at https://onsidearena.com/data/graded-predictions.csv is licensed under CC-BY-4.0. This repository does not vendor that dataset.

## onside-football-mcp

Repository: https://github.com/wr275/onside-football-mcp

Pinned inspected commit: `cf48d1d3374768de5cb6f7716d7e76f06b16e0b6`

npm version: `0.2.0`

License in upstream repository: MIT.

The current FPL adapter does not copy or modify upstream MCP source code. The pin exists to make the capability boundary reproducible: at the inspected commit the MCP exposes World Cup tools, not an FPL expected-points tool.

## Official Fantasy Premier League

Current identity, position, club, deadline and availability sanity checks use the public Official FPL bootstrap endpoint. Official FPL data remains authoritative for those fields; Onside is used only as an independent projection source.
