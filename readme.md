# Tacto Negotiation Arena

Industrial Agents Hackathon - TACTO CHALLENGE

:tacto:  About our challenge:
https://arena.tacto.ai/
The live score board for today: https://arena.tacto.ai/live?tv=1

You apply & sign-up for our track here https://arena.tacto.ai/apply

--- starter kit

https://github.com/tacto-playground/negotiation-arena-kit

### starter-kit/.env
ARENA_URL=https://arena.tacto.ai
ARENA_API_KEY=<your key>

```sh
uv run arena me
uv run arena play case:P01 -v
```

### Starter point

```sh
cp .env ./starter_kit/starter-kit/.env # copy env data from https://arena.tacto.ai

cd ./starter_kit/starter-kit

uv sync --all-extras            # install
uv run arena me                 # your team and its practice quota
uv run arena play case:P01      # the naive example agent negotiates one practice case
uv run arena board              # your team on the practice leaderboard

```