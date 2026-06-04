const queryInput = document.getElementById("q");
const queryMetaNode = document.getElementById("query-meta");
const searchForm = document.getElementById("search-form");
const statusNode = document.getElementById("status");
const resultsNode = document.getElementById("results");
const chips = document.querySelectorAll(".chip");

function setStatus(message, tone = "neutral") {
  statusNode.textContent = message;
  statusNode.dataset.tone = tone;
}

function renderQueryMeta(parsed) {
  const bits = [];

  if (parsed.genre_name) {
    bits.push(`Genre: ${parsed.genre_name}`);
  }
  if (parsed.year) {
    bits.push(`Year: ${parsed.year}`);
  }
  if (parsed.search_text) {
    bits.push(`Title keywords: ${parsed.search_text}`);
  }

  queryMetaNode.textContent = bits.length ? bits.join(" • ") : "Using broad popularity search.";
}

function createPoster(movie) {
  if (movie.poster) {
    const image = document.createElement("img");
    image.src = movie.poster;
    image.alt = movie.title;
    image.loading = "lazy";
    return image;
  }

  const placeholder = document.createElement("div");
  placeholder.className = "poster-placeholder";
  placeholder.textContent = "No poster";
  return placeholder;
}

function createMovieCard(movie) {
  const card = document.createElement("article");
  card.className = "card";

  const poster = createPoster(movie);
  const body = document.createElement("div");
  body.className = "card-body";

  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = movie.title;

  const overview = document.createElement("p");
  overview.className = "card-overview";
  overview.textContent = movie.overview || "No synopsis available for this result.";

  const meta = document.createElement("div");
  meta.className = "card-meta";

  const year = document.createElement("span");
  year.textContent = movie.release_date || "N/A";

  const rating = document.createElement("span");
  rating.className = "rating";
  rating.textContent = `⭐ ${movie.rating || "N/A"}`;

  meta.append(year, rating);
  body.append(title, overview, meta);
  card.append(poster, body);

  return card;
}

async function doSearch(queryOverride) {
  const query = (queryOverride ?? queryInput.value).trim();
  resultsNode.innerHTML = "";

  if (!query) {
    queryMetaNode.textContent = "";
    setStatus("Please enter a search query.", "error");
    return;
  }

  queryInput.value = query;
  setStatus("Searching...", "loading");
  queryMetaNode.textContent = "Parsing your prompt...";

  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || data.error || "Search failed.");
    }

    renderQueryMeta(data.parsed);

    if (!data.results.length) {
      setStatus("No results found.", "neutral");
      return;
    }

    setStatus(`Found ${data.results.length} movies`, "success");

    const grid = document.createElement("div");
    grid.className = "grid";

    data.results.forEach((movie) => {
      grid.appendChild(createMovieCard(movie));
    });

    resultsNode.appendChild(grid);
  } catch (error) {
    console.error(error);
    queryMetaNode.textContent = "";
    setStatus(error.message || "Something went wrong. Please try again.", "error");
  }
}

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  doSearch();
});

chips.forEach((chip) => {
  chip.addEventListener("click", () => doSearch(chip.dataset.query));
});
