function render(el, brief) {
  // ruleid: kit-no-innerhtml
  el.innerHTML = brief.summary;
  // ruleid: kit-no-innerhtml
  el.insertAdjacentHTML("beforeend", brief.summary);
  // ruleid: kit-no-innerhtml
  document.write(brief.summary);
  // ok: kit-no-innerhtml
  el.innerHTML = "";
  // ok: kit-no-innerhtml
  el.textContent = brief.summary;
}
