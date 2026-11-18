# La Polla del Miedo ⚽

> ## About this copy
>
> This is a sanitized public mirror of a private repo. The 2026 World Cup is
> over and the automation is retired: nothing here runs on a schedule or
> submits anything.
>
> Removed or replaced before publishing:
>
> - Pool members' names and nicknames (the standings snapshot is gone and the
>   test samples use "Player 1..3").
> - Account identifiers: the AWS account id (now the placeholder
>   `123456789012` in `infra/dynamodb.tf`) and the pool's own ids and URL.
> - From the pool site integration: the login flow, the endpoints, the submit
>   request and the notes on how they were found. Kept: the data shapes and
>   the read-only page parsers in `src/polla/toqui`. `ToquiClient` is a stub
>   that raises `NotImplementedError`. The jobs in `jobs/` therefore do not run as
>   is; they are kept for reference and are tested against a fake client.
> - The GitHub Actions workflows that refreshed ratings and submitted picks.
>   Only a test workflow remains.
>
> The model, the reports and the backtest (`reports/realism_backtest.md`) are
> unchanged. The prediction changelog stops on 19 July 2026, the day of the
> final. The git history starts at this snapshot.

## Data and credits

- **Team ratings** (`data/ratings.yaml`): World Football Elo Ratings,
  [eloratings.net](https://www.eloratings.net). See
  `data/ratings_sources.md` for why that source was chosen.
- **Fixtures** (`data/fixtures.yaml`): Wikipedia's 2026 FIFA World Cup pages,
  read through the MediaWiki API. Wikipedia text and data are licensed
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
- **Match results and pool scoring**: read from the pool's pages on
  [Toqui](https://toqui.app) while the tournament was on.
- **Method**: the Dixon-Coles adjustment to independent Poisson scores, from
  Dixon and Coles (1997), "Modelling Association Football Scores and
  Inefficiencies in the Football Betting Market".

📋 **[Prediction changelog →](reports/CHANGELOG.md)** · 🔮 **[Current picks →](reports/group_stage.md)**

Statistical predictions + auto-submit for a private **World Cup 2026** pool on
Toqui (toqui.app).

## What it does
- Models every match with a **Dixon-Coles Poisson** grid anchored to team **Elo**, then
  picks the scoreline that maximizes expected pool points (3-1-0, configurable).
- `config/preferences.yaml` is the human control panel: pin scorelines, set the risk knob,
  list your teams. Overrides always beat the model.
- In the private repo, GitHub Actions submitted picks to Toqui before each lock and ran a
  daily refresh, with DynamoDB for shared state. That part is stubbed here (see above).

## Quickstart
```bash
uv sync
uv run python scripts/build_fixtures.py        # data/fixtures.yaml from Wikipedia
uv run python scripts/predict_first_matches.py  # -> reports/first_matches.md
uv run pytest -q
```

## Layout
```
src/polla/   config · models · poisson · ratings · predict · report
scripts/     build_fixtures · predict_first_matches
data/        fixtures.yaml · ratings.yaml
config/      preferences.yaml      (your control panel)
infra/       dynamodb.tf           (Terraform, state table)
tests/
```

Secrets live in `.env` (`API_FOOTBALL_KEY`, AWS). The file is git-ignored.
