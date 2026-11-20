// Reveal elements as they scroll into view. No dependencies, no build step.
// Elements with class="reveal" fade up once; reduced-motion users see them at once.
(function () {
  "use strict";

  var items = document.querySelectorAll(".reveal");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduce || !("IntersectionObserver" in window)) {
    items.forEach(function (el) { el.classList.add("in"); });
    return;
  }

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("in");
        io.unobserve(entry.target);
      }
    });
  }, { rootMargin: "0px 0px -10% 0px", threshold: 0.12 });

  items.forEach(function (el) { io.observe(el); });
})();

// Tap a disabled button on a touch device to see its tooltip reason.
// (Disabled buttons swallow clicks, so listen for touch/click on the parent.)
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("button.disabled");
    if (!btn) { return; }
    btn.classList.toggle("tapped");
    setTimeout(function () { btn.classList.remove("tapped"); }, 2500);
  });
})();
