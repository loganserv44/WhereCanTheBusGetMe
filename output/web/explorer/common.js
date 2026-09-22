// Shared by both pages: the band legend and the footer, both built from data.js so the
// feed version and routing parameters a reader sees are the ones that produced the maps,
// and so the two pages can never disagree about them.
//
// Each page passes the one line that is specific to it (how to drive that page), or
// nothing.
(function () {
  const D = window.EXPLORER_DATA;

  window.buildChrome = function buildChrome(hint) {
    const legend = document.getElementById("legend");
    const footer = document.getElementById("footer");

    if (!D) {
      if (footer) {
        footer.textContent = "Could not load data.js — it must sit next to this page.";
      }
      return false;
    }

    if (legend) {
      legend.innerHTML = D.bands
        .map((b) => `<span><i class="sw" style="background:${b.color}"></i>${b.label}</span>`)
        .join("");
    }

    if (footer) {
      footer.innerHTML =
        `Scheduled StarTran service, feed ${D.feed_version} — not real-time performance. ` +
        `Travel times are the median across a ${D.window_minutes}-minute departure window, ` +
        `so they include a typical wait. Walking ${D.walking_mph} mph, up to a ` +
        `${D.max_walk_minutes}-minute walk to and from stops. Gaps inside a shaded area are ` +
        `places too far from any stop to walk to. Map data © OpenStreetMap contributors. ` +
        (hint ? `<br>${hint} ` : "") +
        `<a href="https://github.com/loganserv44/WhereCanTheBusGetMe">Method and code</a>.`;
    }

    return true;
  };
})();
