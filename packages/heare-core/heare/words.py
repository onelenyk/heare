"""The words of a page, in the person's language: one catalog, two pages.

The brain's page and the keeper's setup form both mark their words the same
way — data-t in the markup, tr() and trn() in the script — and both are
handed out through ``page``: the catalog of the chosen language and the
few lines that read it go in where the page says ``/*WORDS*/``. A word
with no translation stays English.
"""

from __future__ import annotations

import gettext
import json

from heare.core import i18n

_HELPERS = """/* The words. English is the key; the brain puts the person's language here
   as it hands the page out (heare.core.i18n). A word with no translation
   stays English. Markup marks its words with data-t (the text) and
   data-t-title / data-t-placeholder / data-t-aria (an attribute). */
const I18N = %s;
const fill = (s, vars) => vars ? s.replace(/\\{(\\w+)\\}/g, (m, k) => k in vars ? vars[k] : m) : s;
const tr = (key, vars) => fill(typeof I18N[key] === "string" ? I18N[key] : key, vars);
const trn = (n, one, other, vars) => {
  const forms = I18N[one], d = n % 10, h = n % 100;
  const i = !Array.isArray(forms) ? (n === 1 ? 0 : 1)
    : document.documentElement.lang === "uk" ? (d === 1 && h !== 11 ? 0 : d >= 2 && d <= 4 && (h < 12 || h > 14) ? 1 : 2)
    : (n === 1 ? 0 : 1);
  const s = Array.isArray(forms) ? forms[Math.min(i, forms.length - 1)] : [one, other][i];
  return fill(s, {n, ...vars});
};
document.querySelectorAll("[data-t]").forEach(e => { e.textContent = tr(e.textContent.trim()); });
for (const [mark, attr] of [["title", "title"], ["placeholder", "placeholder"], ["aria", "aria-label"]])
  document.querySelectorAll(`[data-t-${mark}]`).forEach(e => e.setAttribute(attr, tr(e.getAttribute(attr))));
"""


def catalog(lang: str) -> dict[str, str | list[str]]:
    """Every word of *lang*, English key to text; a line with plural forms
    maps to the list of them, in the catalog's order."""
    found = gettext.translation(i18n.DOMAIN, i18n.LOCALE, [lang], fallback=True)
    said: dict[str, str | list[str]] = {}
    plural: dict[str, dict[int, str]] = {}
    for key, value in getattr(found, "_catalog", {}).items():
        if isinstance(key, tuple):
            plural.setdefault(key[0], {})[key[1]] = value
        elif key and value:
            said[key] = value
    for key, forms in plural.items():
        said[key] = [forms[n] for n in sorted(forms)]
    return said


def page(html: str, lang: str | None = None) -> str:
    """*html* with its words in *lang* (the chosen one when not given)."""
    lang = lang or i18n.language()
    words = json.dumps(catalog(lang), ensure_ascii=False).replace("</", "<\\/")
    return (html.replace("/*WORDS*/", _HELPERS.replace("%s", words), 1)
            .replace('<html lang="uk">', f'<html lang="{lang}">', 1))
