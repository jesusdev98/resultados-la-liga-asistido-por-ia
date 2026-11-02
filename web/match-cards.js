// Stateless card renderers shared by the chat lifecycle and DOM integration tests.
export function safeTeamLogo(value) {
  if (typeof value !== "string") return "";
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "a.espncdn.com" &&
      !url.username && !url.password && (!url.port || url.port === "443") ? url.href : "";
  } catch {
    return "";
  }
}

export function appendFixtureCard(container, match, teamLogos = {}) {
  if (!match || typeof match !== "object") return;
  const article = document.createElement("article");
  article.className = "bubble bubble-match";
  const teams = document.createElement("div");
  teams.className = "fixture-card-teams";
  for (const team of [match.local, match.visitante].filter(Boolean)) {
    const item = document.createElement("span");
    item.className = "verified-crest";
    const logo = safeTeamLogo(teamLogos[team]);
    if (logo) {
      const image = document.createElement("img");
      image.src = logo;
      image.alt = "";
      image.title = team;
      image.referrerPolicy = "no-referrer";
      image.width = 36;
      image.height = 36;
      image.addEventListener("error", () => image.remove());
      item.appendChild(image);
    }
    const name = document.createElement("span");
    name.className = "fixture-team-name";
    name.textContent = team;
    item.appendChild(name);
    teams.appendChild(item);
  }
  const date = document.createElement("p");
  date.className = "fixture-card-date";
  date.textContent = [match.dia, match.hora].filter(Boolean).join(" · ") || "Fecha por confirmar";
  const status = document.createElement("p");
  status.className = "fixture-card-status";
  status.textContent = match.horario_confirmado === true
    ? "Fecha y horario confirmados"
    : "Fecha provisional · horario por confirmar";
  article.append(teams, date, status);
  container.appendChild(article);
  container.scrollTop = container.scrollHeight;
}

export function appendResultCard(container, result, teamLogos = {}) {
  if (!result || typeof result !== "object") return;
  const article = document.createElement("article");
  article.className = "bubble bubble-match";
  const teams = document.createElement("div");
  teams.className = "fixture-card-teams result-card-teams";
  for (const team of [result.local, result.visitante].filter(Boolean)) {
    const item = document.createElement("span");
    item.className = "verified-crest";
    const logo = safeTeamLogo(teamLogos[team]);
    if (logo) {
      const image = document.createElement("img");
      image.src = logo;
      image.alt = "";
      image.title = team;
      image.referrerPolicy = "no-referrer";
      image.width = 36;
      image.height = 36;
      image.addEventListener("error", () => image.remove());
      item.appendChild(image);
    }
    const name = document.createElement("span");
    name.textContent = team;
    item.appendChild(name);
    teams.appendChild(item);
  }
  const score = document.createElement("strong");
  score.className = "result-card-score";
  score.textContent = `${result.goles_local}-${result.goles_visitante}`;
  const date = document.createElement("p");
  date.className = "fixture-card-date";
  date.textContent = result.dia || "Fecha no disponible";
  const status = document.createElement("p");
  status.className = "fixture-card-status";
  status.textContent = "Finalizado";
  article.append(teams, score, date, status);
  container.appendChild(article);
  container.scrollTop = container.scrollHeight;
}
