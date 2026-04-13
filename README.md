# Movie MCP Server 🎬

A Model Context Protocol (MCP) server that connects Claude AI to The Movie Database (TMDB) API, enabling real-time movie search, details, and recommendations through natural language conversation.

## What it does

This MCP server gives Claude three tools:
- **search_movies** — Search for movies by title or keyword
- **get_movie_details** — Get detailed info (rating, runtime, genres, overview) for any movie
- **get_recommendations** — Get movie recommendations based on a movie you like

## Demo

Once connected, you can ask Claude things like:
- *"Find me highly rated sci-fi movies from 2024"*
- *"Get me details about Dune Part Two"*
- *"What movies are similar to Inception?"*

## Tech Stack

- Python 3.14
- [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) — Anthropic's open protocol for connecting Claude to external tools
- [TMDB API](https://www.themoviedb.org/documentation/api) — Free movie database API
- httpx — Async HTTP client
- FastMCP — Python MCP server framework

## Setup

### 1. Clone the repo
```bash
git clone https://github.com/YOUR_USERNAME/movie-mcp-server.git
cd movie-mcp-server
```

### 2. Create virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install mcp httpx python-dotenv
```

### 3. Get a TMDB API key
- Sign up free at [themoviedb.org](https://www.themoviedb.org/signup)
- Go to Settings → API → Create → Developer
- Copy your API Read Access Token

### 4. Create a .env file
```
TMDB_TOKEN=your_token_here
```

### 5. Connect to Claude Desktop
Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "movie-assistant": {
      "command": "/path/to/venv/bin/python3",
      "args": ["/path/to/movie-mcp-server/server.py"],
      "env": {
        "TMDB_TOKEN": "your_token_here"
      }
    }
  }
}
```

Restart Claude Desktop and start chatting!

## Project Structure
```
movie-mcp-server/
├── server.py        # MCP server with all three tools
├── .env             # Your TMDB token (not committed)
├── .gitignore       # Ignores .env and venv
└── README.md        # This file
```

## Built by

Samara — CS Master's student  
Built as a portfolio project while completing Anthropic Academy courses.