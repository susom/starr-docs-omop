/*
 * Release notes — collapsible years and months on the Release Notes page.
 *
 * scripts/generate_release_notes.py writes that page as plain headings, and
 * Quarto wraps each heading and what follows it in a section:
 *
 *   section.level1#year-2026             a year
 *     section.level2#release-2026-10-15  a release, titled with its month
 *       section.level3#star-12696        one change
 *
 * This script turns each year and release heading into a button that shows or
 * hides the rest of its section. The headings, anchors, table of contents and
 * search index stay as Quarto rendered them, and without JavaScript every
 * section is simply open. With it:
 *
 *   - the newest year and the newest release start open, the rest closed, so
 *     the page opens on what changed last and every older month is one line;
 *   - a link into a closed section opens it first, whether it comes from the
 *     index, the table of contents, a site search result or a shared URL;
 *   - where the browser supports `hidden="until-found"`, find-in-page searches
 *     closed sections too and opens the one it lands in;
 *   - Expand all and Collapse all act on every year and release at once;
 *   - printing shows every section (see the print rules in styles.css);
 *   - a snake_case name in a table wraps after an underscore, not mid-word.
 */
(function () {
  "use strict";

  /* The generator's year and release anchors. tests/test_release_notes.py
     checks that these prefixes still match the ones it writes. */
  var SECTIONS =
    "main section.level1[id^='year-'], main section.level2[id^='release-']";

  /* A closed section the browser can reveal stays searchable with
     find-in-page. Without that support it is plain `hidden`. */
  var UNTIL_FOUND = "onbeforematch" in document.documentElement;

  var toggles = [];
  var byId = {};

  /* The fragment arrives verbatim from whoever sent the link, and
     decodeURIComponent throws on a malformed escape such as `#%zz`. */
  function decodeId(raw) {
    try {
      return decodeURIComponent(raw);
    } catch (_) {
      return raw;
    }
  }

  function plural(count, noun) {
    return count + " " + noun + (count === 1 ? "" : "s");
  }

  function isOpen(toggle) {
    return toggle.button.getAttribute("aria-expanded") === "true";
  }

  function setOpen(toggle, open) {
    toggle.button.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      toggle.panel.removeAttribute("hidden");
    } else {
      toggle.panel.setAttribute("hidden", UNTIL_FOUND ? "until-found" : "");
    }
  }

  /* Opens every closed year and release around the element with this id, and
     the element itself when it is one. Returns the element, or null when the
     page has no such id, and whether anything had to open. */
  function reveal(id) {
    var target = id ? document.getElementById(id) : null;
    var opened = false;
    var node, toggle;
    for (node = target; node; node = node.parentElement) {
      toggle = node.tagName === "SECTION" ? byId[node.id] : null;
      if (toggle && !isOpen(toggle)) {
        setOpen(toggle, true);
        opened = true;
      }
    }
    return { target: target, opened: opened };
  }

  function build(section) {
    var heading = section.firstElementChild;
    if (!heading || !/^H[12]$/.test(heading.tagName)) {
      return null; /* Not the shape the generator writes: leave it open. */
    }
    var level = heading.tagName === "H1" ? 1 : 2;

    var panel = document.createElement("div");
    panel.className = "rn-panel";
    panel.id = "rn-panel-" + section.id;
    while (heading.nextSibling) {
      panel.appendChild(heading.nextSibling);
    }
    section.appendChild(panel);

    var button = document.createElement("button");
    button.type = "button";
    button.className = "rn-toggle";
    button.setAttribute("aria-controls", panel.id);

    var chevron = document.createElement("span");
    chevron.className = "rn-chevron";
    chevron.setAttribute("aria-hidden", "true");
    button.appendChild(chevron);

    /* The heading's own text goes inside the button. anchor.js's link, when
       it is already there, stays outside: a link inside a button is neither
       valid nor reachable. */
    var label = document.createElement("span");
    label.className = "rn-label";
    Array.prototype.slice.call(heading.childNodes).forEach(function (node) {
      if (!(node.classList && node.classList.contains("anchorjs-link"))) {
        label.appendChild(node);
      }
    });
    button.appendChild(label);

    /* What a closed section holds, so an older month says how much it hides. */
    var count = panel.querySelectorAll(
      level === 1 ? ":scope > section.level2" : ":scope > section.level3"
    ).length;
    var meta = document.createElement("span");
    meta.className = "rn-meta";
    meta.textContent = plural(count, level === 1 ? "release" : "change");
    if (count) {
      button.appendChild(meta);
    }

    heading.insertBefore(button, heading.firstChild);
    heading.classList.add("rn-heading");

    var toggle = { button: button, panel: panel, level: level };
    button.addEventListener("click", function () {
      setOpen(toggle, !isOpen(toggle));
    });
    /* Find-in-page found a match in this closed section. The browser shows
       it; the button has to agree. */
    panel.addEventListener("beforematch", function () {
      setOpen(toggle, true);
    });
    return toggle;
  }

  /* Table cells are mostly snake_case column names, and four of them side by
     side are wider than the page. Instead of splitting a name mid-word, or
     hiding a column past the edge of a table that scrolls, let a name wrap
     after an underscore: `ext_death_` / `org_name`. A <wbr> adds nothing to
     copied text. Without this script names stay whole and the table scrolls. */
  var SNAKE = /[A-Za-z0-9]_(?=[A-Za-z0-9])/g;

  function wrapAfterUnderscores(cell) {
    var walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    var nodes = [];
    while (walker.nextNode()) {
      SNAKE.lastIndex = 0;
      if (SNAKE.test(walker.currentNode.nodeValue)) {
        nodes.push(walker.currentNode);
      }
    }
    nodes.forEach(function (node) {
      var text = node.nodeValue;
      var fragment = document.createDocumentFragment();
      var start = 0;
      var match;
      SNAKE.lastIndex = 0;
      while ((match = SNAKE.exec(text))) {
        fragment.appendChild(
          document.createTextNode(text.slice(start, match.index + 2))
        );
        fragment.appendChild(document.createElement("wbr"));
        start = match.index + 2;
      }
      fragment.appendChild(document.createTextNode(text.slice(start)));
      node.parentNode.replaceChild(fragment, node);
    });
  }

  function toolbar() {
    var bar = document.createElement("div");
    bar.className = "rn-toolbar";
    [
      ["Expand all", true],
      ["Collapse all", false]
    ].forEach(function (spec) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-sm btn-outline-secondary rn-button";
      button.textContent = spec[0];
      button.addEventListener("click", function () {
        toggles.forEach(function (toggle) {
          setOpen(toggle, spec[1]);
        });
      });
      bar.appendChild(button);
    });
    return bar;
  }

  function init() {
    if (!document.body.classList.contains("release-notes")) {
      return;
    }
    Array.prototype.forEach.call(
      document.querySelectorAll(
        "main section.level3 th, main section.level3 td"
      ),
      wrapAfterUnderscores
    );
    Array.prototype.forEach.call(
      document.querySelectorAll(SECTIONS),
      function (section) {
        var toggle = build(section);
        if (toggle) {
          toggles.push(toggle);
          byId[section.id] = toggle;
        }
      }
    );
    if (!toggles.length) {
      return;
    }

    /* The generator writes the newest first, so the first year and the first
       release are the ones to leave open. */
    var seen = {};
    toggles.forEach(function (toggle) {
      setOpen(toggle, !seen[toggle.level]);
      seen[toggle.level] = true;
    });

    var first = toggles[0].button.closest("section");
    first.parentNode.insertBefore(toolbar(), first);

    /* Same-page links -- the index, the table of contents -- open their target
       before the browser follows them, so it scrolls to where the target now
       is. Capturing runs this ahead of any handler on the link itself. A link
       to the hash already in the URL fires no hashchange, so this is also what
       reopens a month that was closed after it was first visited. */
    document.addEventListener(
      "click",
      function (event) {
        var link = event.target.closest ? event.target.closest("a[href]") : null;
        if (
          link &&
          link.hash &&
          link.host === window.location.host &&
          link.pathname === window.location.pathname
        ) {
          reveal(decodeId(link.hash.slice(1)));
        }
      },
      true
    );

    function scrollToTarget(element) {
      if (!element) {
        return;
      }
      var header = document.querySelector("#quarto-header");
      var offset = header ? Math.max(0, header.getBoundingClientRect().bottom) : 0;
      var rect = element.getBoundingClientRect();
      var top;
      if (rect.top < offset || rect.bottom > window.innerHeight) {
        top =
          rect.top +
          (window.pageYOffset || document.documentElement.scrollTop) -
          offset -
          10;
        window.scrollTo(0, Math.max(0, top));
      }
    }

    /* Back, forward, or a hash set by other script: the browser has already
       scrolled, to wherever the target was while it was hidden. */
    window.addEventListener("hashchange", function () {
      var shown = reveal(decodeId(window.location.hash.slice(1)));
      if (shown.opened && shown.target) {
        scrollToTarget(shown.target);
      }
    });

    /* A shared link or a site search result (`?q=...#release-...`). Opening
       the target now lets the browser's own jump find it. Scroll to it again
       once the page has loaded: closing the other months moved things, and
       Quarto reserves room for the navbar above a target only after this
       script has run (without that, Firefox leaves it under the navbar). */
    var landed = reveal(decodeId(window.location.hash.slice(1)));
    function settle() {
      if (landed.target) {
        scrollToTarget(landed.target);
      }
    }
    if (document.readyState === "complete") {
      settle();
    } else {
      window.addEventListener("load", settle);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
