function goToStock(event) {
  event.preventDefault();
  const input = document.getElementById("ticker-input");
  const ticker = input.value.trim().toUpperCase();
  if (!ticker) return false;
  window.location.href = "/stock/" + encodeURIComponent(ticker);
  return false;
}

async function loadPremarket(el) {
  const ticker = el.dataset.ticker;
  const pivot  = el.dataset.pivot;
  const stop   = el.dataset.stop;

  let url = "/api/premarket/" + encodeURIComponent(ticker);
  const params = [];
  if (pivot) params.push("pivot=" + encodeURIComponent(pivot));
  if (stop)  params.push("stop=" + encodeURIComponent(stop));
  if (params.length) url += "?" + params.join("&");

  const target = el.classList.contains("premarket-content")
    ? el
    : (el.querySelector(".premarket-content") || el);

  try {
    const resp = await fetch(url);
    const data = await resp.json();

    if (!data.available) {
      target.textContent = "無 pre-market 數據";
      return;
    }

    let text = `昨收：${data.prev_close} / Pre-market：${data.change_pct > 0 ? "+" : ""}${data.change_pct}%`;
    if (typeof data.dist_to_pivot_pct !== "undefined") {
      text += ` / 距Pivot：${data.dist_to_pivot_pct > 0 ? "+" : ""}${data.dist_to_pivot_pct}%`;
    }

    target.innerHTML = text;
    if (data.alert) {
      const span = document.createElement("div");
      span.className = "alert";
      span.textContent = data.alert;
      target.appendChild(span);
    }
  } catch (e) {
    target.textContent = "讀取失敗";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".premarket[data-ticker]").forEach(loadPremarket);
});
