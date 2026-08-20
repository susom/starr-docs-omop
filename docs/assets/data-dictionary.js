/*
 * Data dictionary grids — progressive enhancement for omop_data_dictionary.qmd.
 *
 * Every table on that page is rendered by scripts/generate_exports.py as plain
 * semantic HTML inside a `.dd-static` wrapper. This script reads those tables
 * out of the DOM and replaces each one with a Tabulator grid: frozen identifier
 * columns, per-column header filters, resizable columns, and CSV export of the
 * filtered view.
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

  /* The 43 per-table grids are built a few at a time after the two grids at
     the top of the page, so a slow device stays responsive while they land. */
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
    });
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

    /* All Fields and Tables are what the page opens on, so build them now.
       The 40-odd per-table grids are all below the fold, and building them in
       the same pass would block the main thread on work nobody is looking at
       yet — so they are backfilled one per idle callback instead. Doing it on
       idle rather than on scroll means every grid is guaranteed to exist
       without depending on the visitor scrolling to it, and the browser's
       default scroll anchoring absorbs the height change. */
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
