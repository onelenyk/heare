(function () {
  "use strict";

  var params = new URLSearchParams(window.location.search);
  var requested = params.get("lang");
  var lang = requested === "uk" ? "uk" : "en";
  var dictionary = null;

  function pick(table, key) {
    if (!table || !Object.prototype.hasOwnProperty.call(table, key)) return null;
    return table[key];
  }

  function localizeLinks() {
    document.querySelectorAll("a[data-lang-link]").forEach(function (link) {
      var href = link.getAttribute("href");
      if (!href) return;
      var url;
      try { url = new URL(href, window.location.href); } catch (error) { return; }
      if (url.origin !== window.location.origin) return;
      if (lang === "uk") url.searchParams.set("lang", "uk");
      else url.searchParams.delete("lang");
      link.setAttribute("href", url.pathname + url.search + url.hash);
    });
  }

  function markSwitch() {
    document.querySelectorAll("[data-lang]").forEach(function (button) {
      var active = button.getAttribute("data-lang") === lang;
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function apply(table) {
    dictionary = table;
    var words = table[lang] || table.en;
    document.documentElement.lang = lang;

    document.querySelectorAll("[data-i18n]").forEach(function (node) {
      var value = pick(words, node.getAttribute("data-i18n"));
      if (value != null) node.textContent = value;
    });

    document.querySelectorAll("[data-i18n-html]").forEach(function (node) {
      var value = pick(words, node.getAttribute("data-i18n-html"));
      if (value != null) node.innerHTML = value;
    });

    document.querySelectorAll("[data-i18n-attr]").forEach(function (node) {
      node.getAttribute("data-i18n-attr").split(";").forEach(function (pair) {
        var split = pair.indexOf(":");
        if (split < 0) return;
        var name = pair.slice(0, split).trim();
        var value = pick(words, pair.slice(split + 1).trim());
        if (value != null) node.setAttribute(name, value);
      });
    });

    markSwitch();
    localizeLinks();
  }

  function remember() {
    var url;
    try { url = new URL(window.location.href); } catch (error) { return; }
    url.searchParams.set("lang", lang);
    try { window.history.replaceState(null, "", url.pathname + url.search + url.hash); }
    catch (error) { /* file:// pages may refuse history changes; the switch still works */ }
  }

  function set(next) {
    if (next !== "en" && next !== "uk") return;
    lang = next;
    if (dictionary) apply(dictionary);
    remember();
    document.dispatchEvent(new CustomEvent("hearelang", { detail: { lang: lang } }));
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-lang]") : null;
    if (!button) return;
    event.preventDefault();
    set(button.getAttribute("data-lang"));
  });

  markSwitch();
  localizeLinks();

  window.HeareSite = {
    get lang() { return lang; },
    apply: apply,
    set: set,
    localizeLinks: localizeLinks,
  };
})();
