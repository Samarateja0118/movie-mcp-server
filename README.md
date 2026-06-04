# Movie MCP Server + Live Demo

This repo contains two working entrypoints around the same TMDB integration:

- `server.py`: an MCP server with movie search, details, and recommendation tools
- `webapp.py`: a recruiter-friendly Flask web UI for plain-English movie search

## Demo Ideas

For the MCP server, you can ask for things like:

- `Find me highly rated sci-fi movies from 2024`
- `Get me details about Dune Part Two`
- `What movies are similar to Inception?`
- `top rated movies in 2000's`

For the live web demo, try:

- `scary movies from 2022`
- `Dune 2024`
- `funny comedy 2024`
- `romantic movies 2022`

## Prerequisites

- Python 3.11+
- A TMDB read access token from [themoviedb.org](https://www.themoviedb.org/settings/api)

## Setup

1. Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create your local environment file:

```bash
cp .env.example .env
```

4. Edit `.env` and set:

```bash
TMDB_TOKEN=your_tmdb_read_api_bearer_token_here
```

## Run The Web App

```bash
python webapp.py
```

Open [http://localhost:5000](http://localhost:5000).

## Deploy The Live Demo On Vercel

This repo is prepared for Vercel's Flask deployment flow:

- `app.py` exports the Flask `app` for Vercel's automatic detection
- `public/static/` holds static assets so Vercel can serve them directly
- `.python-version` pins the Python runtime
- `.vercelignore` keeps local-only files out of the deployment

Steps:

1. Push this repo to GitHub.
2. In Vercel, create a new project from the repo.
3. In the Vercel dashboard, add the `TMDB_TOKEN` environment variable in Project Settings.
4. Deploy. Vercel should detect Flask automatically with no extra build config.
5. After deploy, open `/health` on the deployed URL to confirm the app is healthy.

Useful docs:

- [Flask on Vercel](https://vercel.com/docs/frameworks/backend/flask)
- [Python runtime on Vercel](https://vercel.com/docs/functions/runtimes/python)
- [Environment Variables](https://vercel.com/docs/environment-variables)
- [`.vercelignore`](https://vercel.com/docs/deployments/vercel-ignore)

## Run The MCP Server

```bash
python server.py
```

To connect it to Claude Desktop, point the config at your virtualenv Python and this repo's `server.py`:

```json
{
  "mcpServers": {
    "movie-assistant": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/movie-mcp-server/server.py"],
      "env": {
        "TMDB_TOKEN": "your_tmdb_read_api_bearer_token_here"
      }
    }
  }
}
```

## What Was Fixed

- Removed stray shell heredoc markers that were breaking `webapp.py`, `templates/index.html`, and the browser script
- Restored missing runtime dependencies for the MCP server in `requirements.txt`
- Added friendlier TMDB error handling and timeouts
- Improved search parsing so title-plus-year queries like `Dune 2024` work more reliably
- Added a basic live-demo rate limit and query length cap to protect the public URL
- Prepared the app for Vercel hosting

## Quick Verification

Run these from the repo root:

```bash
python -m unittest discover -s tests -v
python webapp.py
```
