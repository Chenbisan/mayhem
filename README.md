# Mayhem Builds

Per-champion **ARAM: Mayhem** augments in one place: the full augment list for each champion (op.gg rank, Blitz tier, rarity, description), sortable and searchable, plus the #1 core build and top boots.

Static site (no build step, no dependencies) + a stdlib-only Python scraper that refreshes `data/` when run.

## Sources

| What | From |
|---|---|
| Ranked augments, #1 core build, top boots | [op.gg ARAM: Mayhem](https://op.gg/lol/modes/aram-mayhem) public pages (`/items`, `/augments`) |
| Champion Mayhem tier, per-champion augment and boot tiers (T1 = best … T5), augment descriptions | [Blitz](https://blitz.gg) ARAM: Mayhem champion page |
| Patch, champion list, item/champion/spell icons | Riot Data Dragon |
| Rarity + icon fallback for augments Blitz hasn't catalogued yet | CommunityDragon `cherry-augments.json` |

op.gg gives order (rank), not percentages, on its public pages. Blitz tiers are its own rating per champion.

## Updating

Manual, on request (no scheduled job):

```sh
python3 scripts/update.py                  # all champions minus config.json "exclude"
python3 scripts/update.py --only jinx,ahri
```

Then commit and push `data/`. A full run is ~520 requests at 1 s spacing (~9 min). A champion whose fetch or parse fails keeps its previous data; the run exits non-zero if more than 15% fail (usually a site layout change → fix the parser in `scripts/update.py`). The machine running it needs access to op.gg, blitz.gg, ddragon.leagueoflegends.com and raw.communitydragon.org.

## Skipping champions

`config.json`:

```json
{ "exclude": ["yuumi", "teemo"], "delay": 1.0 }
```

Slugs are Data Dragon ids lower-cased (`monkeyking` = Wukong, `nunu`, `renata`). Excluded champions aren't fetched and drop out of the list. In the app, **Hide** on a champion page hides it on that device and the home page shows the matching `config.json` text to copy.

## Local

```sh
python3 -m http.server 8000   # then open http://localhost:8000
```

`fetch()` needs a server; opening `index.html` from the file system won't load the data.
