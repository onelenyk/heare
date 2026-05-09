'use strict';

// DOM helpers injected via chrome.scripting.executeScript({files:["content_script.js"]})
// Each function is assigned to window so background.js can call them by name,
// or this file can be injected with executeScript({files:[...]}) and helpers
// are available on the page's window for the duration of that injection.

window.__heareReadPage = function () {
  return {
    url: location.href,
    title: document.title,
    text: document.body.innerText.slice(0, 50000),
  };
};

window.__heareClick = function (selector) {
  const el = document.querySelector(selector);
  if (!el) {
    return {ok: false, error: {code: 'SELECTOR_NOT_FOUND', message: 'No element matches selector "' + selector + '"'}};
  }
  el.click();
  return {ok: true, result: {clicked: true}};
};

window.__heareFill = function (selector, value) {
  const el = document.querySelector(selector);
  if (!el) {
    return {ok: false, error: {code: 'SELECTOR_NOT_FOUND', message: 'No element matches selector "' + selector + '"'}};
  }
  el.value = value;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true, result: {filled: true}};
};

window.__heareExtract = function (selector) {
  const nodes = Array.from(document.querySelectorAll(selector)).slice(0, 100);
  const elements = nodes.map(function (el) {
    const attrs = {};
    for (let i = 0; i < el.attributes.length; i++) {
      attrs[el.attributes[i].name] = el.attributes[i].value;
    }
    return {tag: el.tagName.toLowerCase(), text: el.innerText, attrs: attrs};
  });
  return {ok: true, result: {elements: elements}};
};
