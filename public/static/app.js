const queryInput = document.getElementById("q");
const queryMetaNode = document.getElementById("query-meta");
const searchForm = document.getElementById("search-form");
const statusNode = document.getElementById("status");
const resultsNode = document.getElementById("results");
const chips = document.querySelectorAll(".chip");
const regionSelect = document.getElementById("region");
const panel = document.getElementById("watch-panel");
const panelBody = document.getElementById("watch-body");
const panelClose = document.getElementById("watch-close");
const panelTitle = document.getElementById("watch-title");

let lastFocused = null;

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
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `Where to watch ${movie.title}`);
  card.addEventListener("click", () => openWatchPanel(movie));
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openWatchPanel(movie);
    }
  });

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

  const cue = document.createElement("span");
  cue.className = "card-cue";
  cue.textContent = "Where to watch →";

  meta.append(year, rating);
  body.append(title, overview, meta, cue);
  card.append(poster, body);

  return card;
}

/* ---------------------------------------------------------------- *
 * Where-to-watch panel                                              *
 * ---------------------------------------------------------------- */

function createProviderList(label, providers) {
  if (!providers || !providers.length) {
    return null;
  }

  const group = document.createElement("div");
  group.className = "provider-group";

  const heading = document.createElement("p");
  heading.className = "provider-label";
  heading.textContent = label;

  const row = document.createElement("ul");
  row.className = "provider-row";

  providers.forEach((provider) => {
    const item = document.createElement("li");
    item.className = "provider";

    if (provider.logo) {
      const logo = document.createElement("img");
      logo.src = provider.logo;
      logo.alt = "";
      logo.loading = "lazy";
      item.appendChild(logo);
    }

    const name = document.createElement("span");
    name.textContent = provider.name;
    item.appendChild(name);
    row.appendChild(item);
  });

  group.append(heading, row);
  return group;
}

function renderWatch(watch) {
  const wrap = document.createElement("div");
  wrap.className = "watch";

  if (!watch) {
    const note = document.createElement("p");
    note.className = "watch-empty";
    note.textContent = "Watch availability is unavailable right now.";
    wrap.appendChild(note);
    return wrap;
  }

  const groups = [
    createProviderList("Stream", watch.stream),
    createProviderList("Free", watch.free),
    createProviderList("Rent", watch.rent),
    createProviderList("Buy", watch.buy),
  ].filter(Boolean);

  if (!groups.length) {
    const note = document.createElement("p");
    note.className = "watch-empty";
    note.textContent = `No streaming, rental, or purchase options listed in ${watch.region}.`;
    wrap.appendChild(note);
  } else {
    groups.forEach((group) => wrap.appendChild(group));
  }

  const footer = document.createElement("p");
  footer.className = "watch-footer";

  if (watch.link) {
    const link = document.createElement("a");
    link.href = watch.link;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "View all options";
    footer.append(link, document.createTextNode(" • "));
  }

  footer.appendChild(document.createTextNode(`${watch.attribution}.`));
  wrap.appendChild(footer);
  return wrap;
}

function closePanel() {
  panel.hidden = true;
  panelBody.innerHTML = "";
  if (lastFocused) {
    lastFocused.focus();
  }
}

async function openWatchPanel(movie) {
  lastFocused = document.activeElement;
  panel.hidden = false;
  panelTitle.textContent = movie.title;
  panelBody.innerHTML = "";

  const loading = document.createElement("p");
  loading.className = "watch-empty";
  loading.textContent = "Checking where you can watch this...";
  panelBody.appendChild(loading);
  panelClose.focus();

  try {
    const region = regionSelect.value;
    const response = await fetch(`/api/movies/${movie.id}?region=${encodeURIComponent(region)}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || data.error || "Could not load watch options.");
    }

    panelBody.innerHTML = "";

    const subtitle = document.createElement("p");
    subtitle.className = "panel-subtitle";
    const bits = [data.detail.release_date ? data.detail.release_date.slice(0, 4) : null];
    if (data.detail.runtime_minutes) bits.push(`${data.detail.runtime_minutes} min`);
    if (data.detail.genres.length) bits.push(data.detail.genres.join(", "));
    bits.push(`Region: ${region}`);
    subtitle.textContent = bits.filter(Boolean).join(" • ");

    panelBody.append(subtitle, renderWatch(data.watch));
  } catch (error) {
    console.error(error);
    panelBody.innerHTML = "";
    const failed = document.createElement("p");
    failed.className = "watch-empty";
    failed.textContent = error.message || "Could not load watch options.";
    panelBody.appendChild(failed);
  }
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

panelClose.addEventListener("click", closePanel);

panel.addEventListener("click", (event) => {
  if (event.target === panel) {
    closePanel();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !panel.hidden) {
    closePanel();
  }
});
