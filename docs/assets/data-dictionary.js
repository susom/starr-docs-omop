/*
 * Data dictionary grids — progressive enhancement for omop_data_dictionary.qmd.
 *
 * Every table on that page is rendered by scripts/generate_exports.py as plain
 * semantic HTML inside a `.dd-static` wrapper. This script does two things to
 * that markup:
 *
 *   - replaces each table with a Tabulator grid: frozen identifier columns,
 *     per-column header filters, resizable columns, and CSV export of the
 *     filtered view;
 *   - turns the page's 45 sections into tab panels behind a vertical rail, so
 *     reaching one table is a click rather than a long scroll.
 *
 * Reading from the DOM rather than from a separate JSON payload is deliberate:
 *
 *   - the static tables stay in the rendered HTML, so Quarto's site search and
 *     llms-full.txt still index all fields;
 *   - printing and no-JS visitors fall back to a real table (see the print
 *     rules in styles.css);
 *   - there is no second copy of the data to drift out of sync with the
 *     workbook, and generate_exports.py's contract is unchanged.
 *
 * Column behaviour is keyed off the header text, and grid behaviour off the
 * `dd-layout-*` class the generator puts on each table, so neither side has to
 * hard-code column positions.
 */
(function () {
  "use strict";

  /* Descriptions run from a few words to ~950 characters (median ~105). Longer
     ones are clamped to two lines and expand on click, so one verbose field
     cannot make a row twelve lines tall. */
  var CLAMP_CHARS = 140;
  var SEARCH_DEBOUNCE_MS = 120;

  /* Only used by the no-tabs fallback, which builds every grid a few at a
     time so a slow device stays responsive while they land. */
  var IDLE_CHUNK = 4;
  var IDLE_TIMEOUT_MS = 100;

  /* Per-column presentation, keyed by the header text emitted by the
     generator. Anything not listed here renders as a plain sortable column. */
  var COLUMN_TRAITS = {
    Table: { width: 200, mono: true, identifier: true, headerFilter: "input" },
    Category: { width: 140, headerFilter: "list" },
    Field: { width: 240, mono: true, identifier: true, headerFilter: "input" },
    Required: {
      title: "Req",
      width: 80,
      hozAlign: "center",
      headerFilter: "list",
      headerFilterValues: ["", "Yes", "No"]
    },
    Type: { width: 125, mono: true, headerFilter: "list" },
    Fields: { width: 90, hozAlign: "right", sorter: "number", numeric: true },
    Description: {
      widthGrow: 5,
      minWidth: 320,
      headerFilter: "input",
      clamp: true
    }
  };

  /* Per-grid behaviour, keyed by the `dd-layout-*` class.
     `freeze` pins the identifier columns while scrolling sideways — only worth
     it on the six-column All Fields grid, which is the only one wide enough to
     scroll. `virtual` renders just the visible rows; `basic` puts every row in
     the DOM so browser find-in-page still works on the smaller grids. */
  var LAYOUTS = {
    "dd-layout-all": {
      label: "all fields",
      csv: "starr_omop_all_fields",
      height: "72vh",
      freeze: true,
      render: "virtual"
    },
    "dd-layout-index": {
      label: "tables",
      csv: "starr_omop_tables",
      maxHeight: "70vh",
      render: "basic"
    },
    "dd-layout-fields": {
      label: "fields",
      render: "basic"
    }
  };

  function layoutFor(table) {
    for (var name in LAYOUTS) {
      if (table.classList.contains(name)) {
        return LAYOUTS[name];
      }
    }
    return null;
  }

  function fieldName(header) {
    return header.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  }

  function hrefField(field) {
    return field + "__href";
  }

  function isDataField(key) {
    return key !== "_i" && key.indexOf("__href") === -1;
  }

  /* ---------------------------------------------------------------- read */

  function readTable(table) {
    var headers = [];
    table.querySelectorAll("thead th").forEach(function (th) {
      headers.push(th.textContent.trim());
    });

    var rows = [];
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      var cells = tr.querySelectorAll("td");
      if (cells.length !== headers.length) {
        return;
      }
      var row = { _i: rows.length };
      headers.forEach(function (header, index) {
        var trait = COLUMN_TRAITS[header] || {};
        var cell = cells[index];
        var field = fieldName(header);
        var text = cell.textContent.trim();
        row[field] = trait.numeric ? Number(text) : text;
        /* The Tables index links each name to its section further down the
           page. Keep the target as its own field so the grid can rebuild the
           anchor while CSV export still gets clean text. */
        var link = cell.querySelector("a[href]");
        if (link) {
          row[hrefField(field)] = link.getAttribute("href");
        }
      });
      rows.push(row);
    });

    return { headers: headers, rows: rows };
  }

  /* ---------------------------------------------------------- formatters */

  function linkFormatter(field) {
    return function (cell) {
      var href = cell.getRow().getData()[hrefField(field)];
      if (!href) {
        return cell.getValue();
      }
      var anchor = document.createElement("a");
      anchor.href = href;
      anchor.textContent = cell.getValue();
      return anchor;
    };
  }

  /* Expanded descriptions are tracked by row index rather than by DOM state:
     the All Fields grid recycles cell elements as you scroll, so a class left
     on the element alone would not survive. */
  function descriptionFormatter(expanded) {
    return function (cell) {
      var value = cell.getValue() || "";
      var element = document.createElement("div");
      element.className = "dd-desc";
      element.textContent = value;
      if (value.length > CLAMP_CHARS) {
        var open = expanded.has(cell.getRow().getIndex());
        element.classList.add("dd-desc-clamped");
        element.classList.toggle("dd-desc-open", open);
        element.title = open ? "Click to collapse" : value;
      }
      return element;
    };
  }

  function descriptionToggle(expanded) {
    return function (event, cell) {
      var element = cell.getElement().querySelector(".dd-desc");
      if (!element || !element.classList.contains("dd-desc-clamped")) {
        return;
      }
      var key = cell.getRow().getIndex();
      var open = !expanded.has(key);
      if (open) {
        expanded.add(key);
      } else {
        expanded.delete(key);
      }
      element.classList.toggle("dd-desc-open", open);
      element.title = open ? "Click to collapse" : cell.getValue();
      cell.getRow().normalizeHeight();
    };
  }

  /* ------------------------------------------------------------- columns */

  function buildColumns(headers, rows, layout, expanded) {
    return headers.map(function (header) {
      var trait = COLUMN_TRAITS[header] || {};
      var field = fieldName(header);
      var column = {
        title: trait.title || header,
        field: field,
        headerTooltip: header,
        minWidth: trait.minWidth || 70
      };

      if (trait.width) {
        column.width = trait.width;
      }
      if (trait.widthGrow) {
        column.widthGrow = trait.widthGrow;
      }
      if (trait.hozAlign) {
        column.hozAlign = trait.hozAlign;
      }
      if (trait.sorter) {
        column.sorter = trait.sorter;
      }
      if (trait.mono) {
        column.cssClass = "dd-mono";
      }
      if (layout.freeze && trait.identifier) {
        column.frozen = true;
      }

      if (trait.headerFilter === "list") {
        column.headerFilter = "list";
        column.headerFilterFunc = "=";
        column.headerFilterParams = trait.headerFilterValues
          ? { values: trait.headerFilterValues, clearable: true }
          : { valuesLookup: true, sort: "asc", clearable: true };
      } else if (trait.headerFilter) {
        column.headerFilter = "input";
      }

      if (trait.clamp) {
        column.formatter = descriptionFormatter(expanded);
        column.cellClick = descriptionToggle(expanded);
        column.variableHeight = true;
      } else if (rows.some(function (row) { return row[hrefField(field)]; })) {
        column.formatter = linkFormatter(field);
      }

      return column;
    });
  }

  /* ------------------------------------------------------------- toolbar */

  function button(label, title) {
    var element = document.createElement("button");
    element.type = "button";
    element.className = "btn btn-sm btn-outline-secondary dd-button";
    element.textContent = label;
    element.title = title;
    return element;
  }

  function buildToolbar(grid, total, layout, csvName) {
    var bar = document.createElement("div");
    bar.className = "dd-toolbar";

    var search = document.createElement("input");
    search.type = "search";
    search.className = "dd-search";
    search.placeholder = "Search " + total + " " + layout.label + "…";
    search.setAttribute("aria-label", "Search " + layout.label);

    var clear = button("Clear", "Clear the search box and every column filter");
    var download = button(
      "Download (.csv)",
      "Download the rows currently shown, in the current order"
    );
    var count = document.createElement("span");
    count.className = "dd-count";

    bar.appendChild(search);
    bar.appendChild(clear);
    bar.appendChild(download);
    bar.appendChild(count);

    function updateCount(shown) {
      /* `dataFiltered` hands us the surviving rows. Preferring them over
         getDataCount("active") matters: the active-row cache is not yet
         repopulated when the event fires, so reading it here reports the
         previous filter's total. */
      if (typeof shown !== "number") {
        shown = grid.getDataCount("active");
      }
      count.textContent =
        shown === total
          ? total + " " + layout.label
          : shown + " of " + total + " " + layout.label;
    }

    var timer = null;
    search.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        var needle = search.value.trim().toLowerCase();
        if (!needle) {
          grid.clearFilter(false);
          return;
        }
        grid.setFilter(function (data) {
          for (var key in data) {
            if (
              isDataField(key) &&
              String(data[key]).toLowerCase().indexOf(needle) !== -1
            ) {
              return true;
            }
          }
          return false;
        });
      }, SEARCH_DEBOUNCE_MS);
    });

    clear.addEventListener("click", function () {
      search.value = "";
      /* `true` also resets the per-column header filters, not just the
         programmatic one set by the search box. */
      grid.clearFilter(true);
    });

    download.addEventListener("click", function () {
      grid.download("csv", csvName + ".csv", {}, "active");
    });

    grid.on("tableBuilt", function () {
      updateCount();
    });
    grid.on("dataFiltered", function (filters, rows) {
      updateCount(rows.length);
    });

    return bar;
  }

  /* ------------------------------------------------------------- enhance */

  function enhance(table) {
    var layout = layoutFor(table);
    var container = table.closest(".dd-static");
    if (!layout || !container || container.dataset.ddEnhanced) {
      return;
    }
    container.dataset.ddEnhanced = "true";

    var source = readTable(table);
    if (!source.rows.length) {
      return;
    }

    var expanded = new Set();
    var host = document.createElement("div");
    host.className = "dd-grid";

    var options = {
      data: source.rows,
      index: "_i",
      columns: buildColumns(source.headers, source.rows, layout, expanded),
      layout: "fitColumns",
      renderVertical: layout.render,
      movableColumns: true,
      resizableColumnFit: false,
      placeholder: "No matching rows",
      columnDefaults: { headerFilterLiveFilter: true, resizable: true }
    };
    if (layout.height) {
      options.height = layout.height;
    }
    if (layout.maxHeight) {
      options.maxHeight = layout.maxHeight;
    }

    var grid = new Tabulator(host, options);
    /* Name per-table downloads after the table itself, e.g. `dt-measurement`
       becomes `starr_omop_measurement.csv`. */
    var csvName =
      layout.csv || "starr_omop_" + table.id.replace(/^dt-/, "");

    container.parentNode.insertBefore(
      buildToolbar(grid, source.rows.length, layout, csvName),
      container
    );
    container.parentNode.insertBefore(host, container);

    grid.on("tableBuilt", function () {
      /* Only retire the static table once the grid actually rendered, so a
         Tabulator failure leaves a readable page rather than a blank one. */
      container.hidden = true;
      host.dataset.ddReady = "true";
    });
  }

  /* ---------------------------------------------------------------- tabs */

  /* The page is 45 sections long — All Fields, the table index, and one per
     OMOP table — which is a lot of scrolling to reach PROCEDURE_OCCURRENCE.
     They become tab panels behind a vertical rail instead.

     The rail is vertical rather than a horizontal tab strip because 43 names
     of 20-odd characters wrap to six or seven rows and push the content below
     the fold. Same interaction, a control that survives the item count. */

  function sectionTable(section) {
    return section.querySelector(".dd-static table.data-dictionary-table");
  }

  /* The index grid already knows each table's category and field count, so
     the rail reads them from there rather than recomputing. */
  function readIndexMeta() {
    var meta = {};
    var index = document.querySelector("table.dd-layout-index");
    if (!index) {
      return meta;
    }
    index.querySelectorAll("tbody tr").forEach(function (tr) {
      var cells = tr.querySelectorAll("td");
      var link = cells.length > 2 && cells[0].querySelector("a[href^='#']");
      if (link) {
        meta[link.getAttribute("href").slice(1)] = {
          category: cells[1].textContent.trim(),
          count: cells[2].textContent.trim()
        };
      }
    });
    return meta;
  }

  function collectSections(main) {
    return Array.prototype.filter.call(main.children, function (element) {
      return (
        element.tagName === "SECTION" && element.id && sectionTable(element)
      );
    });
  }

  function groupSections(sections, meta) {
    var groups = [];

    function group(name) {
      for (var i = 0; i < groups.length; i++) {
        if (groups[i].name === name) {
          return groups[i];
        }
      }
      groups.push({ name: name, items: [] });
      return groups[groups.length - 1];
    }

    group("Overview");

    sections.forEach(function (section) {
      var heading = section.querySelector("h2");
      var info = meta[section.id] || {};
      var overview = section.id === "all-fields" || section.id === "tables";
      group(overview ? "Overview" : info.category || "Tables").items.push({
        id: section.id,
        label: heading ? heading.textContent.trim() : section.id,
        count:
          info.count ||
          String(section.querySelectorAll(".dd-static tbody tr").length),
        section: section
      });
    });

    return groups.filter(function (entry) {
      return entry.items.length;
    });
  }

  function railButton(item) {
    var element = document.createElement("button");
    element.type = "button";
    element.className = "dd-rail-item";
    element.id = "dd-tab-" + item.id;
    element.setAttribute("role", "tab");
    element.setAttribute("aria-controls", item.id);
    element.setAttribute("aria-selected", "false");
    element.tabIndex = -1;

    var name = document.createElement("span");
    name.className = "dd-rail-name";
    name.textContent = item.label;

    var count = document.createElement("span");
    count.className = "dd-rail-count";
    count.textContent = item.count;

    element.appendChild(name);
    element.appendChild(count);
    return element;
  }

  /* Re-measure a panel that is being shown again. Tabulator builds
     asynchronously and throws if asked to redraw before it has finished, so
     this waits for the `dd-ready` flag that enhance() sets on `tableBuilt`.
     A grid built while its panel was already visible needs no redraw at all. */
  function redraw(section) {
    var host = section.querySelector(".dd-grid[data-dd-ready]");
    var grid = host && Tabulator.findTable(host);
    if (grid && grid.length) {
      grid[0].redraw(true);
    }
  }

  function buildTabs() {
    var main = document.querySelector("main");
    var sections = main ? collectSections(main) : [];
    if (sections.length < 3) {
      return false; /* Not the shape we expect — leave the page alone. */
    }

    var groups = groupSections(sections, readIndexMeta());
    var items = [];
    var byId = {};

    var browser = document.createElement("div");
    browser.className = "dd-browser";

    var rail = document.createElement("nav");
    rail.className = "dd-rail";
    rail.setAttribute("aria-label", "Data dictionary sections");

    var filter = document.createElement("input");
    filter.type = "search";
    filter.className = "dd-rail-filter";
    filter.placeholder = "Filter " + (sections.length - 2) + " tables…";
    filter.setAttribute("aria-label", "Filter the list of tables");

    var list = document.createElement("div");
    list.className = "dd-rail-list";
    list.setAttribute("role", "tablist");
    list.setAttribute("aria-orientation", "vertical");

    var empty = document.createElement("p");
    empty.className = "dd-rail-empty";
    empty.textContent = "No table matches that filter.";
    empty.hidden = true;

    groups.forEach(function (entry) {
      var heading = document.createElement("p");
      heading.className = "dd-rail-group";
      heading.textContent = entry.name;
      list.appendChild(heading);
      entry.group = heading;

      entry.items.forEach(function (item) {
        item.button = railButton(item);
        item.group = entry;
        list.appendChild(item.button);
        items.push(item);
        byId[item.id] = item;
      });
    });

    rail.appendChild(filter);
    rail.appendChild(list);
    rail.appendChild(empty);

    var panels = document.createElement("div");
    panels.className = "dd-panels";

    main.insertBefore(browser, sections[0]);
    browser.appendChild(rail);
    browser.appendChild(panels);

    sections.forEach(function (section) {
      section.setAttribute("role", "tabpanel");
      section.setAttribute("aria-labelledby", "dd-tab-" + section.id);
      section.hidden = true;
      panels.appendChild(section);
    });

    var selected = null;

    function activate(id, focus) {
      var item = byId[id];
      if (!item) {
        return false;
      }
      selected = item;
      items.forEach(function (other) {
        var on = other === item;
        other.button.setAttribute("aria-selected", on ? "true" : "false");
        other.button.tabIndex = on ? 0 : -1;
        other.section.hidden = !on;
      });
      /* Built on activation rather than up front: a grid needs real layout to
         size its columns, so there is nothing to gain from building one inside
         a hidden panel — and everything to lose. */
      var table = sectionTable(item.section);
      if (table) {
        enhance(table);
      }
      redraw(item.section);
      if (focus) {
        item.button.focus();
      }
      /* replaceState, not location.hash: the URL stays shareable without
         adding a history entry per click or re-triggering a scroll. */
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, "", "#" + id);
      }
      return true;
    }

    items.forEach(function (item) {
      item.button.addEventListener("click", function () {
        activate(item.id);
      });
    });

    function visibleItems() {
      return items.filter(function (item) {
        return !item.button.hidden;
      });
    }

    list.addEventListener("keydown", function (event) {
      var steps = { ArrowDown: 1, ArrowUp: -1 };
      var shown = visibleItems();
      var current = shown.indexOf(byId[document.activeElement.id.slice(7)]);
      var next = null;

      if (event.key in steps && current !== -1) {
        next = shown[(current + steps[event.key] + shown.length) % shown.length];
      } else if (event.key === "Home") {
        next = shown[0];
      } else if (event.key === "End") {
        next = shown[shown.length - 1];
      }

      if (next) {
        event.preventDefault();
        activate(next.id, true);
      }
    });

    filter.addEventListener("input", function () {
      var needle = filter.value.trim().toLowerCase();
      var shown = 0;
      items.forEach(function (item) {
        var on = !needle || item.label.toLowerCase().indexOf(needle) !== -1;
        item.button.hidden = !on;
        shown += on ? 1 : 0;
      });
      groups.forEach(function (entry) {
        entry.group.hidden = entry.items.every(function (item) {
          return item.button.hidden;
        });
      });
      empty.hidden = shown > 0;

      /* Filtering out the selected tab would otherwise leave its panel open
         with no selected button beside it — a tablist with nothing selected,
         and arrow keys with no anchor to move from. Move the selection to the
         first tab still standing. Focus is deliberately not taken: the filter
         field has it and the visitor is still typing. Enhancing is cached, so
         a tab already built costs nothing to re-activate on later keystrokes.

         When nothing matches, there is no tab to move to; the panel stays put
         behind the "no match" message and comes back into agreement with the
         rail as soon as the filter admits anything again. */
      if (selected && selected.button.hidden) {
        var first = visibleItems()[0];
        if (first) {
          activate(first.id);
        }
      }
    });

    function fromHash() {
      var id = (window.location.hash || "").replace(/^#/, "");
      return byId[id] ? id : null;
    }

    window.addEventListener("hashchange", function () {
      var id = fromHash();
      /* Links in the index grid point at sections that are now hidden panels,
         so the browser's own jump does nothing and we do it here instead. */
      if (id && activate(id)) {
        browser.scrollIntoView({ block: "start" });
      }
    });

    var initial = fromHash();
    activate(initial || items[0].id);
    if (initial) {
      browser.scrollIntoView({ block: "start" });
    }
    return true;
  }

  /* ---------------------------------------------------------------- init */

  function enhanceEverything(tables) {
    /* Fallback for a page shape the tab builder did not recognise: the two
       grids at the top are what you land on, and the rest are backfilled a few
       per idle callback so the main thread stays free. */
    var deferred = [];
    tables.forEach(function (table) {
      if (layoutFor(table) === LAYOUTS["dd-layout-fields"]) {
        deferred.push(table);
      } else {
        enhance(table);
      }
    });

    var idle =
      window.requestIdleCallback ||
      function (callback) {
        return window.setTimeout(callback, 16);
      };

    (function next() {
      if (!deferred.length) {
        return;
      }
      /* The `timeout` matters as much as the idle callback itself: a page
         that never reports an idle period would otherwise stop backfilling
         part-way through and leave the remaining sections as static tables. */
      idle(
        function () {
          var chunk = IDLE_CHUNK;
          while (chunk-- && deferred.length) {
            enhance(deferred.shift());
          }
          next();
        },
        { timeout: IDLE_TIMEOUT_MS }
      );
    })();
  }

  function init() {
    if (typeof Tabulator === "undefined") {
      return; /* CDN blocked — the static tables are still on the page. */
    }

    var tables = document.querySelectorAll(
      ".dd-static table.data-dictionary-table"
    );
    if (!tables.length) {
      return;
    }

    if (!buildTabs()) {
      enhanceEverything(tables);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
