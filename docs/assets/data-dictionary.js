/*
 * Data dictionary grids — progressive enhancement for omop_data_dictionary.qmd.
 *
 * Every table on that page is rendered by scripts/generate_exports.py as plain
 * semantic HTML inside a `.dd-static` wrapper. This script does two things to
 * that markup:
 *
 *   - replaces each table with a Tabulator grid: frozen identifier columns,
 *     per-column header filters, a column manager, grouping, a row detail
 *     drawer, and CSV export of the filtered view;
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
 * Column behaviour is not described here at all. Each `<th>` carries its
 * registry entry from generate_exports.py as `data-dd-*` attributes, and this
 * file reads them — so a column added to the registry arrives with its filter,
 * width, alignment, monospace and grouping already configured, and nothing
 * here has to change. Grid behaviour is still keyed off the `dd-layout-*`
 * class, because that is a property of the grid rather than of a column.
 */
(function () {
  "use strict";

  /* Descriptions run from a few words to ~950 characters (median ~105). Longer
     ones are clamped to two lines and expand on click, so one verbose field
     cannot make a row twelve lines tall. */
  var CLAMP_CHARS = 140;
  var SEARCH_DEBOUNCE_MS = 120;

  /* The URL is rewritten as the view changes. Slower than the search debounce
     on purpose: this one only has to settle before somebody copies the URL. */
  var URL_DEBOUNCE_MS = 350;

  /* Only used by the no-tabs fallback, which builds every grid a few at a
     time so a slow device stays responsive while they land. */
  var IDLE_CHUNK = 4;
  var IDLE_TIMEOUT_MS = 100;

  /* Below this width six columns cannot be read side by side whatever you do
     with them, so the grid starts on the Compact preset and the drawer becomes
     the way to read a full row. NARROW_MIN_WIDTH caps the registry's column
     minimums there: desktop sizing would push the field name off the edge of
     a phone before the first paint. */
  var NARROW_QUERY = "(max-width: 767.98px)";
  var NARROW_MIN_WIDTH = 110;

  /* Per-grid behaviour, keyed by the `dd-layout-*` class.
     `freeze` pins the identifier columns while scrolling sideways — only worth
     it on the wide All Fields grid, which is the only one wide enough to
     scroll. `virtual` renders just the visible rows; `basic` puts every row in
     the DOM so browser find-in-page still works on the smaller grids. */
  var LAYOUTS = {
    "dd-layout-all": {
      name: "all",
      label: "all fields",
      csv: "starr_omop_all_fields",
      height: "72vh",
      freeze: true,
      render: "virtual"
    },
    "dd-layout-index": {
      name: "index",
      label: "tables",
      csv: "starr_omop_tables",
      maxHeight: "70vh",
      render: "basic"
    },
    "dd-layout-fields": {
      name: "fields",
      label: "fields",
      render: "basic"
    }
  };

  /* Named column sets. `default` is whatever the generator marked visible, so
     it follows the registry rather than being restated here; `compact` is the
     columns the registry marks `compact`; `everything` is everything. A column
     added to the registry lands in the right presets without touching this. */
  var PRESETS = [
    { id: "default", label: "Default" },
    { id: "compact", label: "Compact" },
    { id: "everything", label: "Everything" }
  ];

  function layoutFor(table) {
    for (var name in LAYOUTS) {
      if (table.classList.contains(name)) {
        return LAYOUTS[name];
      }
    }
    return null;
  }

  function isNarrow() {
    return window.matchMedia && window.matchMedia(NARROW_QUERY).matches;
  }

  /* ------------------------------------------------------- page-level data */

  /* Fields added or retyped since the committed baseline, emitted by
     generate_exports.py as a JSON data island. Absent when there is no
     baseline to compare against, in which case nothing is badged. */
  var CHANGES = (function () {
    var node = document.getElementById("dd-changes");
    if (!node) {
      return null;
    }
    var data;
    try {
      data = JSON.parse(node.textContent);
    } catch (error) {
      return null;
    }
    var added = new Set(data.added || []);
    var changed = new Set(data.changed || []);
    if (!added.size && !changed.size) {
      return null; /* Nothing to badge — do not offer a filter for zero rows. */
    }
    return { since: data.since || "the last release", added: added, changed: changed };
  })();

  /* Upper-case table name -> its section anchor, read from the Tables index.
     The one source of link targets for columns the registry marks
     `links="table"`; a name that is not in here renders as plain text rather
     than as a link to nowhere. */
  var TABLE_LINKS = {};

  function readTableLinks() {
    var index = document.querySelector("table.dd-layout-index");
    if (!index) {
      return;
    }
    index.querySelectorAll("tbody tr td a[href^='#']").forEach(function (link) {
      TABLE_LINKS[link.textContent.trim().toUpperCase()] = link.getAttribute("href");
    });
  }

  /* ------------------------------------------------------------ read specs */

  function attr(th, name) {
    return th.getAttribute("data-dd-" + name);
  }

  function flag(th, name) {
    return attr(th, name) === "true";
  }

  function number(th, name) {
    var value = parseInt(attr(th, name), 10);
    return isNaN(value) ? null : value;
  }

  /* One column's registry entry, as the generator wrote it onto the `<th>`.
     The fallbacks matter: a table rendered by something other than the current
     generator still produces a usable, sortable column. */
  function readColumnSpecs(table) {
    var specs = [];
    table.querySelectorAll("thead th").forEach(function (th) {
      var header = th.textContent.trim();
      var values = null;
      var raw = attr(th, "filter-values");
      if (raw) {
        try {
          values = JSON.parse(raw);
        } catch (error) {
          values = null;
        }
      }
      specs.push({
        header: header,
        key: attr(th, "key") || header.toLowerCase().replace(/[^a-z0-9]+/g, "_"),
        title: attr(th, "title") || header,
        mono: flag(th, "mono"),
        identifier: flag(th, "identifier"),
        numeric: flag(th, "numeric"),
        clamp: flag(th, "clamp"),
        groupable: flag(th, "groupable"),
        compact: flag(th, "compact"),
        hidden: flag(th, "hidden"),
        filter: attr(th, "filter"),
        filterValues: values,
        align: attr(th, "align"),
        links: attr(th, "links"),
        width: number(th, "width"),
        minWidth: number(th, "min-width"),
        grow: number(th, "grow")
      });
    });
    return specs;
  }

  function readRows(table, specs) {
    var rows = [];
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      var cells = tr.querySelectorAll("td");
      if (cells.length !== specs.length) {
        return;
      }
      var row = { _i: rows.length };
      specs.forEach(function (spec, index) {
        var text = cells[index].textContent.trim();
        row[spec.key] = spec.numeric ? Number(text) : text;
      });
      rows.push(row);
    });
    return rows;
  }

  /* Stamps the persistence key with the column set, so adding a column gives
     every returning visitor the new default exactly once instead of stranding
     them on a layout that predates it. */
  function columnsDigest(specs) {
    var source = specs
      .map(function (spec) {
        return spec.key;
      })
      .join(",");
    var hash = 5381;
    for (var i = 0; i < source.length; i++) {
      hash = ((hash << 5) + hash + source.charCodeAt(i)) | 0;
    }
    return (hash >>> 0).toString(36);
  }

  /* ---------------------------------------------------------- formatters */

  /* A cell holding one or more table names, each linked to its section. Built
     with createElement and textContent throughout: the value is dbt data, and
     the only markup here is an anchor this function made whose href came from
     the index table rather than from the cell. */
  function tableLinkFormatter(cell) {
    var value = cell.getValue() || "";
    var fragment = document.createDocumentFragment();
    value.split(",").forEach(function (part, index) {
      var name = part.trim();
      if (!name) {
        return;
      }
      if (index) {
        fragment.appendChild(document.createTextNode(", "));
      }
      var href = TABLE_LINKS[name.toUpperCase()];
      if (!href) {
        fragment.appendChild(document.createTextNode(name));
        return;
      }
      var anchor = document.createElement("a");
      anchor.href = href;
      anchor.textContent = name;
      fragment.appendChild(anchor);
    });
    var host = document.createElement("span");
    host.appendChild(fragment);
    return host;
  }

  function changeKind(state, data) {
    if (!CHANGES) {
      return "";
    }
    /* Key spelling has to match `field_signatures()` exactly: the table is
       upper-cased there, the field keeps whatever case dbt authored it in --
       lower, in practice, for every OMOP column. Upper-casing the whole key
       would turn `PERSON.person_id` into `PERSON.PERSON_ID` and never hit. */
    var table = state.tableName || data.table || "";
    var key = table.toUpperCase() + "." + (data.field || "");
    if (CHANGES.added.has(key)) {
      return "new";
    }
    return CHANGES.changed.has(key) ? "changed" : "";
  }

  /* The field name, plus a badge when it is new or retyped since the baseline.
     Display only — `accessorDownload` runs on the raw value, so the badge
     never reaches the CSV. */
  function fieldFormatter(state) {
    return function (cell) {
      var host = document.createElement("span");
      host.className = "dd-field";
      host.appendChild(document.createTextNode(cell.getValue() || ""));

      var kind = changeKind(state, cell.getRow().getData());
      if (kind) {
        var badge = document.createElement("span");
        badge.className = "dd-badge dd-badge-" + kind;
        badge.textContent = kind;
        badge.title =
          (kind === "new" ? "Added since " : "Type or requiredness changed since ") +
          CHANGES.since;
        host.appendChild(badge);
      }
      return host;
    };
  }

  /* Expanded descriptions are tracked by row index rather than by DOM state:
     the All Fields grid recycles cell elements as you scroll, so a class left
     on the element alone would not survive.

     `expandAll` is the mode, and each mode has its own set of per-row
     exceptions to it: `expanded` while collapsed is the default, `collapsed`
     while expanded is. Two sets rather than one because clicking a single
     description means "this row", not "leave the mode" -- the earlier code
     dropped out of expand-all on a per-row collapse and rerendered only the
     cell that was clicked, leaving every other row visibly expanded under a
     toolbar button that had already flipped back to "Expand all". */
  function descriptionFormatter(state) {
    return function (cell) {
      var value = cell.getValue() || "";

      if (value.length <= CLAMP_CHARS) {
        var plain = document.createElement("div");
        plain.className = "dd-desc";
        plain.textContent = value;
        return plain;
      }

      /* The expander is a real <button>, not a div with a click handler: that
         is what gets it into the tab order, gives it Enter and Space for free,
         and announces it as a control rather than as prose. Driven from a
         Tabulator cellClick the two lines a keyboard visitor can see would be
         all they ever get. The stylesheet strips the button chrome, so it
         still reads as the description it is. */
      var key = cell.getRow().getIndex();
      var element = document.createElement("button");
      element.type = "button";
      element.className = "dd-desc dd-desc-clamped";
      element.textContent = value;

      function render(open) {
        element.classList.toggle("dd-desc-open", open);
        element.setAttribute("aria-expanded", open ? "true" : "false");
        element.title = open ? "Collapse description" : value;
      }

      function isOpen() {
        return state.expandAll
          ? !state.collapsed.has(key)
          : state.expanded.has(key);
      }

      render(isOpen());

      /* Listening on the button rather than on the cell keeps mouse and
         keyboard on one path -- a keyboard activation dispatches a click that
         would otherwise reach a cellClick handler too, and toggle twice. */
      element.addEventListener("click", function (event) {
        /* Without this the click also reaches rowClick and opens the drawer. */
        event.stopPropagation();
        var open = !isOpen();
        if (state.expandAll) {
          /* Stay in expand-all; record this one row as the exception. */
          if (open) {
            state.collapsed.delete(key);
          } else {
            state.collapsed.add(key);
          }
        } else if (open) {
          state.expanded.add(key);
        } else {
          state.expanded.delete(key);
        }
        render(open);
        cell.getRow().normalizeHeight();
      });

      return element;
    };
  }

  /* ------------------------------------------------------------- download */

  /* Excel, Sheets and Numbers all read a cell opening with one of these as the
     start of a formula rather than as text, so a dbt description beginning
     "=..." would execute when somebody opens the CSV this page hands them. */
  var FORMULA_LEAD = /^[=+\-@\t\r]/;

  /* An apostrophe is the conventional neutraliser: spreadsheets take it as
     "treat the rest as text" and do not display it, so the cell still reads as
     written. Applied only on download -- the grid itself renders through
     textContent and was never at risk. */
  function csvSafe(value) {
    return typeof value === "string" && FORMULA_LEAD.test(value)
      ? "'" + value
      : value;
  }

  /* ------------------------------------------------------------- columns */

  function buildColumns(specs, layout, state, narrow) {
    var columns = specs.map(function (spec) {
      var column = {
        title: spec.title,
        field: spec.key,
        headerTooltip: spec.header,
        minWidth: spec.minWidth || 70,
        visible: !state.hidden.has(spec.key)
      };

      /* On a phone the registry's widths are sizing hints, not requirements:
         held to, two columns already overflow a 375px screen and the field
         name — the one thing anyone came for — starts off the right edge. */
      if (narrow) {
        column.minWidth = Math.min(column.minWidth, NARROW_MIN_WIDTH);
      } else if (spec.width) {
        column.width = spec.width;
      }
      if (spec.grow) {
        column.widthGrow = spec.grow;
      }
      if (spec.align) {
        column.hozAlign = spec.align;
      }
      if (spec.numeric) {
        column.sorter = "number";
      }
      if (spec.mono) {
        column.cssClass = "dd-mono";
      }
      /* Freezing costs width, and on a phone there is none to spare — the
         responsive collapse is doing that job instead. */
      if (layout.freeze && spec.identifier && !narrow) {
        column.frozen = true;
      }

      if (spec.filter === "list") {
        column.headerFilter = "list";
        column.headerFilterFunc = "=";
        column.headerFilterParams = spec.filterValues
          ? { values: spec.filterValues, clearable: true }
          : { valuesLookup: true, sort: "asc", clearable: true };
      } else if (spec.filter) {
        column.headerFilter = "input";
      }

      /* Every column, not just the prose ones: a table or field name is dbt
         data too, and the whole point is that no cell reaches a spreadsheet
         as something other than text. */
      column.accessorDownload = csvSafe;

      if (spec.clamp) {
        column.formatter = descriptionFormatter(state);
        column.variableHeight = true;
      } else if (spec.links === "table") {
        column.formatter = tableLinkFormatter;
      } else if (spec.key === "field" && CHANGES) {
        column.formatter = fieldFormatter(state);
      }

      return column;
    });

    return columns;
  }

  /* ------------------------------------------------------------- controls */

  function button(label, title) {
    var element = document.createElement("button");
    element.type = "button";
    element.className = "btn btn-sm btn-outline-secondary dd-button";
    element.textContent = label;
    element.title = title;
    return element;
  }

  /* `shape` draws the mark in CSS instead of typesetting it. Arrow glyphs are
     the obvious way to label these buttons and the wrong one: U+2191 is absent
     from plenty of installed font stacks, and a button that renders as a tofu
     box is worse than no icon at all. `symbol` stays for marks that live in
     Latin-1 and are therefore safe to type. */
  function iconButton(label, title, symbol, shape) {
    var element = button(label, title);
    element.classList.add("dd-icon-button");
    element.setAttribute("aria-label", title);
    if (symbol || shape) {
      var mark = document.createElement("span");
      mark.className = "dd-icon" + (shape ? " dd-icon-" + shape : "");
      mark.setAttribute("aria-hidden", "true");
      if (symbol) {
        mark.textContent = symbol;
      }
      element.insertBefore(mark, element.firstChild);
    }
    return element;
  }

  /* ------------------------------------------------------- columns manager */

  /* The column manager. Tabulator's own `headerMenu` is right-click only,
     which is neither discoverable nor reachable without a mouse, so this is a
     labelled button and a real popover instead. Reordering is up/down buttons
     rather than drag: `movableColumns` already gives the mouse a way to drag a
     header, and buttons are the version a keyboard can use. */
  function buildColumnsMenu(grid, specs, state, onChange) {
    var wrap = document.createElement("div");
    wrap.className = "dd-cols";

    var trigger = button("Columns", "Choose which columns are shown");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-haspopup", "true");

    var menu = document.createElement("div");
    menu.className = "dd-cols-menu";
    menu.hidden = true;

    var presetRow = document.createElement("div");
    presetRow.className = "dd-cols-presets";
    var presetLabel = document.createElement("label");
    presetLabel.className = "dd-cols-preset-label";
    presetLabel.textContent = "Preset";
    var preset = document.createElement("select");
    preset.className = "dd-select";
    PRESETS.forEach(function (entry) {
      var option = document.createElement("option");
      option.value = entry.id;
      option.textContent = entry.label;
      preset.appendChild(option);
    });
    presetLabel.appendChild(preset);
    presetRow.appendChild(presetLabel);
    menu.appendChild(presetRow);

    var list = document.createElement("ul");
    list.className = "dd-cols-list";
    menu.appendChild(list);

    var rows = {};

    function visibleCount() {
      return specs.length - state.hidden.size;
    }

    function syncTrigger() {
      trigger.textContent =
        "Columns (" + visibleCount() + " of " + specs.length + ")";
      trigger.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
    }

    /* Only the rows the popover has actually rendered — it renders lazily on
       first open, and the toolbar syncs state well before that. */
    function syncLocks() {
      var only = visibleCount() === 1;
      specs.forEach(function (spec) {
        var row = rows[spec.key];
        if (!row) {
          return;
        }
        var shown = !state.hidden.has(spec.key);
        row.check.checked = shown;
        /* The grid cannot be emptied: the last column standing keeps its box
           ticked and disabled rather than silently refusing the click. */
        row.check.disabled = only && shown;
        row.check.title = row.check.disabled
          ? "At least one column has to stay visible"
          : "";
      });
    }

    function setVisible(spec, shown) {
      var column = grid.getColumn(spec.key);
      if (!column) {
        return;
      }
      if (shown) {
        state.hidden.delete(spec.key);
        column.show();
      } else {
        state.hidden.add(spec.key);
        /* Trap: Tabulator keeps a hidden column's header filter active, so
           rows stay filtered by a control nobody can see any more. Guarded on
           the spec because asking a column that has no header filter for one
           throws. */
        if (spec.filter) {
          grid.setHeaderFilterValue(spec.key, "");
        }
        column.hide();
      }
    }

    function applyPreset(id) {
      specs.forEach(function (spec) {
        var shown =
          id === "everything" ||
          (id === "compact" ? spec.compact : !spec.hidden);
        setVisible(spec, shown);
      });
      if (!visibleCount()) {
        setVisible(specs[0], true);
      }
      syncLocks();
      syncTrigger();
      onChange();
    }

    /* One step left or right, counted in columns you can actually see.
       Stepping by one position in the full order instead would swap a column
       past a hidden neighbour and appear to do nothing at all. */
    function move(key, delta) {
      var columns = grid.getColumns();
      var order = columns.map(function (column) {
        return column.getField();
      });
      var from = order.indexOf(key);
      if (from === -1) {
        return;
      }
      var to = from + delta;
      while (to >= 0 && to < order.length && !columns[to].isVisible()) {
        to += delta;
      }
      if (to < 0 || to >= order.length) {
        return;
      }
      grid.moveColumn(key, order[to], delta > 0);
      renderList();
      onChange();
      var row = rows[key];
      if (row) {
        (delta > 0 ? row.down : row.up).focus();
      }
    }

    function renderList() {
      list.textContent = "";
      rows = {};
      var columns = grid.getColumns().filter(function (column) {
        var key = column.getField();
        return key && key !== "_i";
      });
      var order = columns.map(function (column) {
        return column.getField();
      });
      /* Whether there is a visible column to swap with in that direction — the
         same question `move` asks, so a button that would do nothing is
         disabled rather than inert. */
      function neighbour(index, delta) {
        for (var at = index + delta; at >= 0 && at < columns.length; at += delta) {
          if (columns[at].isVisible()) {
            return true;
          }
        }
        return false;
      }
      order.forEach(function (key, index) {
        var spec = specs.filter(function (entry) {
          return entry.key === key;
        })[0];
        if (!spec) {
          return;
        }
        var item = document.createElement("li");
        item.className = "dd-cols-item";

        var label = document.createElement("label");
        label.className = "dd-cols-check";
        var check = document.createElement("input");
        check.type = "checkbox";
        check.addEventListener("change", function () {
          setVisible(spec, check.checked);
          syncLocks();
          syncTrigger();
          onChange();
        });
        label.appendChild(check);
        label.appendChild(document.createTextNode(spec.header));

        var up = iconButton("", "Move " + spec.header + " left", "", "left");
        var down = iconButton("", "Move " + spec.header + " right", "", "right");
        up.disabled = !neighbour(index, -1);
        down.disabled = !neighbour(index, 1);
        up.addEventListener("click", function () {
          move(spec.key, -1);
        });
        down.addEventListener("click", function () {
          move(spec.key, 1);
        });

        item.appendChild(label);
        item.appendChild(up);
        item.appendChild(down);
        list.appendChild(item);
        rows[spec.key] = { check: check, up: up, down: down };
      });
      syncLocks();
    }

    preset.addEventListener("change", function () {
      applyPreset(preset.value);
    });

    function close(focus) {
      if (menu.hidden) {
        return;
      }
      menu.hidden = true;
      syncTrigger();
      if (focus) {
        trigger.focus();
      }
    }

    function open() {
      renderList();
      menu.hidden = false;
      syncTrigger();
      var first = menu.querySelector("input, select, button");
      if (first) {
        first.focus();
      }
    }

    trigger.addEventListener("click", function () {
      if (menu.hidden) {
        open();
      } else {
        close(true);
      }
    });

    menu.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        event.stopPropagation();
        close(true);
      }
    });

    trigger.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        close(true);
      }
    });

    document.addEventListener("click", function (event) {
      if (!menu.hidden && !wrap.contains(event.target)) {
        close(false);
      }
    });

    wrap.appendChild(trigger);
    wrap.appendChild(menu);
    syncTrigger();

    return {
      element: wrap,
      sync: function () {
        syncLocks();
        syncTrigger();
      },
      apply: function (keys) {
        specs.forEach(function (spec) {
          setVisible(spec, keys.indexOf(spec.key) !== -1);
        });
        if (!visibleCount()) {
          setVisible(specs[0], true);
        }
        syncLocks();
        syncTrigger();
      }
    };
  }

  /* --------------------------------------------------------------- drawer */

  /* The structural answer to "I need two more columns": a row read vertically
     costs no grid width at all, so columns that will not fit side by side are
     still one click away. */
  function buildDrawer(specs, state) {
    var drawer = document.createElement("aside");
    drawer.className = "dd-drawer";
    drawer.hidden = true;
    drawer.setAttribute("role", "region");
    drawer.setAttribute("aria-label", "Row detail");

    var head = document.createElement("div");
    head.className = "dd-drawer-head";
    var title = document.createElement("p");
    title.className = "dd-drawer-title";
    var close = iconButton("", "Close the row detail", "×");
    head.appendChild(title);
    head.appendChild(close);

    var body = document.createElement("dl");
    body.className = "dd-drawer-body";

    drawer.appendChild(head);
    drawer.appendChild(body);

    var returnFocus = null;

    function hide() {
      drawer.hidden = true;
      if (returnFocus && returnFocus.isConnected) {
        returnFocus.focus();
      }
      returnFocus = null;
    }

    close.addEventListener("click", hide);

    /* Escape closes it wherever focus happens to be. A handler on the drawer
       alone would only serve the keyboard path: clicking a row leaves focus on
       the grid, or on nothing at all, and Escape doing nothing is the first
       thing a visitor tries. Cheap enough to leave bound — an open drawer is
       the only state in which it does anything. */
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !drawer.hidden) {
        hide();
      }
    });

    function show(data, focus, source) {
      /* A per-table grid drops the Table column, so the row's own values spell
         only "provider_id". The heading above says PROVIDER, but the drawer is
         the part people read aloud and paste into tickets, so it carries the
         qualified name itself. */
      var parts = specs
        .filter(function (spec) {
          return spec.identifier;
        })
        .map(function (spec) {
          return data[spec.key];
        })
        .filter(Boolean);
      if (state.tableName && parts[0] !== state.tableName) {
        parts.unshift(state.tableName);
      }
      title.textContent = parts.join(".") || "Row";

      var kind = changeKind(state, data);
      if (kind) {
        var badge = document.createElement("span");
        badge.className = "dd-badge dd-badge-" + kind;
        badge.textContent = kind;
        title.appendChild(document.createTextNode(" "));
        title.appendChild(badge);
      }

      body.textContent = "";
      specs.forEach(function (spec) {
        var value = data[spec.key];
        if (value === undefined || value === null || value === "") {
          return;
        }
        var term = document.createElement("dt");
        term.textContent = spec.header;
        var definition = document.createElement("dd");
        if (spec.links === "table") {
          String(value)
            .split(",")
            .forEach(function (part, index) {
              var name = part.trim();
              if (!name) {
                return;
              }
              if (index) {
                definition.appendChild(document.createTextNode(", "));
              }
              var href = TABLE_LINKS[name.toUpperCase()];
              if (href) {
                var anchor = document.createElement("a");
                anchor.href = href;
                anchor.textContent = name;
                definition.appendChild(anchor);
              } else {
                definition.appendChild(document.createTextNode(name));
              }
            });
        } else {
          definition.textContent = String(value);
        }
        if (spec.mono) {
          definition.classList.add("dd-mono");
        }
        body.appendChild(term);
        body.appendChild(definition);
      });

      drawer.hidden = false;
      if (focus) {
        returnFocus = source || null;
        close.focus();
      }
      /* The drawer sits under a grid that is most of a screen tall, so on the
         click path it opens below the fold and the click reads as having done
         nothing. "nearest" scrolls the least amount that brings it into view,
         which usually keeps the row you clicked on screen too. */
      if (drawer.scrollIntoView) {
        drawer.scrollIntoView({ block: "nearest" });
      }
    }

    return { element: drawer, show: show, hide: hide };
  }

  /* -------------------------------------------------------------- toolbar */

  function buildToolbar(context) {
    var grid = context.grid;
    var specs = context.specs;
    var state = context.state;
    var layout = context.layout;
    var total = context.total;

    var bar = document.createElement("div");
    bar.className = "dd-toolbar";

    var search = document.createElement("input");
    search.type = "search";
    search.className = "dd-search";
    search.setAttribute("aria-label", "Search " + layout.label);

    var count = document.createElement("span");
    count.className = "dd-count";
    /* Without this a screen-reader user typing in the search box gets no
       feedback at all: the rows change and nothing is announced. */
    count.setAttribute("aria-live", "polite");
    count.setAttribute("aria-atomic", "true");

    function syncPlaceholder() {
      var hidden = state.hidden.size;
      search.placeholder = hidden
        ? "Search " +
          total +
          " " +
          layout.label +
          " in " +
          (specs.length - hidden) +
          " of " +
          specs.length +
          " columns…"
        : "Search " + total + " " + layout.label + "…";
      /* Trap: the search used to walk every field, so hiding Description and
         searching for a phrase inside one returned rows with no visible
         reason. It now searches what you can see, and says so. */
      search.title = hidden
        ? "Searches only the columns currently shown"
        : "Searches every column";
    }

    function applyRowFilter() {
      var needle = state.search.trim().toLowerCase();
      if (!needle && !state.changedOnly) {
        /* `false` leaves the per-column header filters alone. */
        grid.clearFilter(false);
        return;
      }
      grid.setFilter(function (data) {
        if (state.changedOnly && !changeKind(state, data)) {
          return false;
        }
        if (!needle) {
          return true;
        }
        for (var i = 0; i < specs.length; i++) {
          var spec = specs[i];
          if (state.hidden.has(spec.key)) {
            continue;
          }
          var value = data[spec.key];
          if (
            value !== undefined &&
            value !== null &&
            String(value).toLowerCase().indexOf(needle) !== -1
          ) {
            return true;
          }
        }
        return false;
      });
    }

    var columns = buildColumnsMenu(grid, specs, state, function () {
      syncPlaceholder();
      applyRowFilter();
      context.pushUrl();
    });

    var group = null;
    var groupable = specs.filter(function (spec) {
      return spec.groupable;
    });
    if (groupable.length) {
      var groupLabel = document.createElement("label");
      groupLabel.className = "dd-group";
      groupLabel.appendChild(document.createTextNode("Group"));
      group = document.createElement("select");
      group.className = "dd-select";
      var none = document.createElement("option");
      none.value = "";
      none.textContent = "none";
      group.appendChild(none);
      groupable.forEach(function (spec) {
        var option = document.createElement("option");
        option.value = spec.key;
        option.textContent = spec.header;
        group.appendChild(option);
      });
      group.addEventListener("change", function () {
        state.group = group.value;
        grid.setGroupBy(state.group || false);
        context.pushUrl();
      });
      groupLabel.appendChild(group);
    }

    var expand = button(
      "Expand all",
      "Show every description in full instead of clamping to two lines"
    );
    expand.setAttribute("aria-pressed", "false");

    var density = button("Compact", "Fit more rows on screen");
    density.setAttribute("aria-pressed", "false");

    var changed = null;
    if (CHANGES) {
      changed = button(
        "Changed",
        "Show only fields added or retyped since " + CHANGES.since
      );
      changed.classList.add("dd-changed-toggle");
      changed.setAttribute("aria-pressed", "false");
    }

    var clear = button("Clear", "Clear the search box and every column filter");
    var download = button(
      "Download (.csv)",
      "Downloads the rows and the columns currently shown, in the current order"
    );

    bar.appendChild(search);
    bar.appendChild(columns.element);
    if (group) {
      bar.appendChild(group.parentNode);
    }
    bar.appendChild(expand);
    bar.appendChild(density);
    if (changed) {
      bar.appendChild(changed);
    }
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

    function syncExpand() {
      expand.setAttribute("aria-pressed", state.expandAll ? "true" : "false");
      expand.textContent = state.expandAll ? "Collapse all" : "Expand all";
      expand.classList.toggle("active", state.expandAll);
    }

    function syncDensity() {
      density.setAttribute("aria-pressed", state.density ? "true" : "false");
      density.classList.toggle("active", !!state.density);
      context.host.classList.toggle("dd-density-compact", !!state.density);
    }

    function syncChanged() {
      if (!changed) {
        return;
      }
      changed.setAttribute("aria-pressed", state.changedOnly ? "true" : "false");
      changed.classList.toggle("active", state.changedOnly);
      changed.textContent =
        "Changed (" + (CHANGES.added.size + CHANGES.changed.size) + ")";
    }

    var timer = null;
    search.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        state.search = search.value;
        applyRowFilter();
        context.pushUrl();
      }, SEARCH_DEBOUNCE_MS);
    });

    expand.addEventListener("click", function () {
      state.expandAll = !state.expandAll;
      /* Both sets hold per-row exceptions to the mode being left, so the
         button always delivers exactly what it says rather than the previous
         mode's exceptions showing through. */
      state.expanded.clear();
      state.collapsed.clear();
      syncExpand();
      grid.redraw(true);
      context.pushUrl();
    });

    density.addEventListener("click", function () {
      state.density = !state.density;
      syncDensity();
      grid.redraw(true);
      context.pushUrl();
    });

    if (changed) {
      changed.addEventListener("click", function () {
        state.changedOnly = !state.changedOnly;
        syncChanged();
        applyRowFilter();
        context.pushUrl();
      });
    }

    clear.addEventListener("click", function () {
      search.value = "";
      state.search = "";
      state.changedOnly = false;
      syncChanged();
      /* `true` also resets the per-column header filters, not just the
         programmatic one set by the search box. */
      grid.clearFilter(true);
      context.pushUrl();
    });

    download.addEventListener("click", function () {
      /* Tabulator omits hidden columns, which is the deliberate choice here:
         the button says it downloads what is shown, and the workbook is the
         route to everything. */
      grid.download("csv", context.csvName + ".csv", {}, "active");
    });

    grid.on("tableBuilt", function () {
      updateCount();
    });
    grid.on("dataFiltered", function (filters, rows) {
      updateCount(rows.length);
    });
    grid.on("headerFilterChanged", function () {
      context.pushUrl();
    });

    syncPlaceholder();
    syncExpand();
    syncDensity();
    syncChanged();

    return {
      element: bar,
      columns: columns,
      applyRowFilter: applyRowFilter,
      syncPlaceholder: syncPlaceholder,
      syncAll: function () {
        syncExpand();
        syncDensity();
        syncChanged();
        columns.sync();
        syncPlaceholder();
      },
      setSearch: function (value) {
        search.value = value;
        state.search = value;
      },
      setGroup: function (value) {
        if (group) {
          group.value = value;
        }
      }
    };
  }

  /* ------------------------------------------------------------ URL state */

  /* The whole view goes in the URL — tab, search, visible columns, grouping,
     density, expanded descriptions and every column filter — so a link pasted
     into a ticket reopens what the sender was looking at, not just the
     section they were in. */
  /* The section whose grid currently owns the URL, and every built grid by
     section id. Both stay null/empty in the no-tabs fallback, where there is
     no single view for one URL to describe — so nothing is written. */
  var activeSection = null;
  var CONTEXTS = {};
  var urlTimer = null;

  /* `decodeURIComponent` throws a URIError on a malformed percent escape, and
     the fragment is the one part of the URL that arrives verbatim from
     whoever sent the link -- `#%zz` is enough. parseHash runs after every
     section has been moved into a hidden tab panel, so an exception escaping
     here aborts tab setup with nothing on screen at all. A fragment that
     cannot be decoded is not an id this page knows: hand back the raw text and
     let the unknown-id path fall through to the default tab.
     (URLSearchParams needs no such guard -- its decoding is lossy, not
     throwing, and leaves an invalid escape as the literal text.) */
  function decodeId(raw) {
    try {
      return decodeURIComponent(raw);
    } catch (error) {
      return raw;
    }
  }

  function parseHash() {
    var raw = (window.location.hash || "").replace(/^#/, "");
    var split = raw.indexOf("?");
    if (split === -1) {
      return { id: decodeId(raw), params: new URLSearchParams() };
    }
    return {
      id: decodeId(raw.slice(0, split)),
      params: new URLSearchParams(raw.slice(split + 1))
    };
  }

  function writeHash(id, params) {
    var query = params.toString();
    var next = "#" + id + (query ? "?" + query : "");
    if (window.history && window.history.replaceState) {
      /* replaceState, not location.hash: the URL stays shareable without
         adding a history entry per keystroke or re-triggering a scroll. */
      window.history.replaceState(null, "", next);
    }
  }

  function serialize(context) {
    var state = context.state;
    var params = new URLSearchParams();
    if (state.search) {
      params.set("q", state.search);
    }
    if (state.group) {
      params.set("g", state.group);
    }
    if (state.density) {
      params.set("d", "1");
    }
    if (state.expandAll) {
      params.set("x", "1");
    }
    if (state.changedOnly) {
      params.set("ch", "1");
    }
    if (state.hidden.size) {
      params.set(
        "c",
        context.specs
          .filter(function (spec) {
            return !state.hidden.has(spec.key);
          })
          .map(function (spec) {
            return spec.key;
          })
          .join(",")
      );
    }
    context.grid.getHeaderFilters().forEach(function (entry) {
      if (entry.value !== "" && entry.value !== null && entry.value !== undefined) {
        params.append("f", entry.field + ":" + entry.value);
      }
    });
    return params;
  }

  function pushUrlFor(context) {
    /* Only the panel on screen describes itself in the URL. A background grid
       finishing a redraw does not get to overwrite it, and the fallback with
       45 grids on one page does not write at all. */
    if (!activeSection || activeSection !== context.sectionId || !context.ready) {
      return;
    }
    window.clearTimeout(urlTimer);
    urlTimer = window.setTimeout(function () {
      writeHash(context.sectionId, serialize(context));
    }, URL_DEBOUNCE_MS);
  }

  function applyUrlState(context, params) {
    var state = context.state;
    var toolbar = context.toolbar;

    var columns = params.get("c");
    if (columns) {
      context.toolbar.columns.apply(columns.split(","));
    }
    var search = params.get("q");
    if (search) {
      toolbar.setSearch(search);
    }
    state.density = params.get("d") === "1";
    state.expandAll = params.get("x") === "1";
    if (!state.expandAll) {
      /* A per-row collapse only means anything inside expand-all. Left behind,
         it would come back the next time the mode is switched on. */
      state.collapsed.clear();
    }
    state.changedOnly = CHANGES ? params.get("ch") === "1" : false;
    var group = params.get("g");
    if (group && context.specs.some(function (spec) {
      return spec.key === group && spec.groupable;
    })) {
      state.group = group;
      toolbar.setGroup(group);
      context.grid.setGroupBy(group);
    }
    params.getAll("f").forEach(function (entry) {
      var at = entry.indexOf(":");
      if (at === -1) {
        return;
      }
      var field = entry.slice(0, at);
      if (context.grid.getColumn(field)) {
        context.grid.setHeaderFilterValue(field, entry.slice(at + 1));
      }
    });

    toolbar.syncAll();
    toolbar.applyRowFilter();
    if (state.density || state.expandAll) {
      context.grid.redraw(true);
    }
  }

  /* ------------------------------------------------------------- enhance */

  function enhance(table, pendingParams) {
    var layout = layoutFor(table);
    var container = table.closest(".dd-static");
    if (!layout || !container) {
      return;
    }
    if (container.dataset.ddEnhanced) {
      return;
    }
    container.dataset.ddEnhanced = "true";

    var specs = readColumnSpecs(table);
    var rows = readRows(table, specs);
    if (!rows.length || !specs.length) {
      return;
    }

    var narrow = isNarrow();

    /* Kept alongside the state so `tableBuilt` can tell a view persistence
       restored from the one the registry would have produced anyway. */
    var defaultHidden = specs
      .filter(function (spec) {
        /* A phone starts on the Compact preset rather than the registry
           default. It is the same set the preset menu offers, so the column
           manager can explain and undo it — unlike Tabulator's responsive
           collapse, which hides columns behind the state the manager and the
           search box both read from. */
        return narrow ? !spec.compact : spec.hidden;
      })
      .map(function (spec) {
        return spec.key;
      });

    var state = {
      hidden: new Set(defaultHidden),
      expanded: new Set(),
      collapsed: new Set(),
      expandAll: false,
      density: false,
      changedOnly: false,
      search: "",
      group: "",
      tableName: table.getAttribute("data-dd-table") || ""
    };

    var host = document.createElement("div");
    host.className = "dd-grid";

    var options = {
      data: rows,
      index: "_i",
      columns: buildColumns(specs, layout, state, narrow),
      layout: "fitColumns",
      renderVertical: layout.render,
      movableColumns: true,
      resizableColumnFit: false,
      placeholder: "No matching rows",
      columnDefaults: { headerFilterLiveFilter: true, resizable: true },
      /* Visibility is remembered, and per layout rather than per table: all 43
         per-table grids share one column set, so a choice made on PERSON
         should hold on MEASUREMENT. Widths are not — a saved width is how a
         column ends up two characters wide on a screen it was never measured
         against.

         The `v<digest>` suffix is over the column keys, so adding a column to
         the registry retires every stored layout that predates it. That is the
         point: a returning visitor gets the new default once, instead of a
         saved set that silently has no opinion about the new column. */
      persistence: { columns: ["visible"] },
      persistenceMode: "local",
      /* Phone and desktop keep separate stores. They start from different
         column sets, and one store would mean a choice made on a phone
         greeting the same person with four columns on their laptop. */
      persistenceID:
        "dd-" +
        layout.name +
        (narrow ? "-narrow" : "") +
        "-v" +
        columnsDigest(specs)
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
    var csvName = layout.csv || "starr_omop_" + table.id.replace(/^dt-/, "");

    var section = table.closest("section");
    var context = {
      grid: grid,
      specs: specs,
      state: state,
      layout: layout,
      host: host,
      total: rows.length,
      csvName: csvName,
      ready: false,
      sectionId: section ? section.id : null,
      pushUrl: function () {
        pushUrlFor(context);
      }
    };

    var drawer = buildDrawer(specs, state);
    var toolbar = buildToolbar(context);
    context.toolbar = toolbar;
    if (context.sectionId) {
      CONTEXTS[context.sectionId] = context;
    }

    container.parentNode.insertBefore(toolbar.element, container);
    container.parentNode.insertBefore(host, container);
    container.parentNode.insertBefore(drawer.element, container);

    grid.on("rowClick", function (event, row) {
      /* The description expander and the table links are controls of their
         own; a click that landed on one is not a request to open the row. */
      if (event.target && event.target.closest("button, a, input, select")) {
        return;
      }
      /* A keyboard-generated click reports detail 0. Only then is moving
         focus into the drawer the right thing to do. */
      drawer.show(row.getData(), event.detail === 0, event.target);
    });

    grid.on("tableBuilt", function () {
      /* Only retire the static table once the grid actually rendered, so a
         Tabulator failure leaves a readable page rather than a blank one. */
      container.hidden = true;
      host.dataset.ddReady = "true";
      context.ready = true;

      /* Persistence may have restored a different set than the registry
         defaults, so the state follows the grid rather than the other way
         round. */
      state.hidden = new Set(
        grid
          .getColumns()
          .filter(function (column) {
            return !column.isVisible();
          })
          .map(function (column) {
            return column.getField();
          })
      );
      toolbar.syncAll();

      if (pendingParams) {
        applyUrlState(context, pendingParams);
        return;
      }

      /* `activate()` wrote a bare `#section` while this grid was still
         building — `ready` was false, and pushUrlFor will not describe a view
         that does not exist yet. Nothing prompts it afterwards either, so a
         column set restored from a previous visit is missing from every URL
         copied off the page until some unrelated control is touched.

         Only when it differs from the registry default, though: writing the
         default set into the fragment would pin today's columns into every
         link anyone shares, and a later change to the defaults would never
         reach them. */
      var restored =
        state.hidden.size !== defaultHidden.length ||
        defaultHidden.some(function (key) {
          return !state.hidden.has(key);
        });
      if (restored) {
        pushUrlFor(context);
      }
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
     the rail reads them from there rather than recomputing. Read by column
     key rather than by position, so the index gaining a column does not
     silently start reporting the wrong number. */
  function readIndexMeta() {
    var meta = {};
    var index = document.querySelector("table.dd-layout-index");
    if (!index) {
      return meta;
    }
    var keys = [];
    index.querySelectorAll("thead th").forEach(function (th) {
      keys.push(attr(th, "key") || th.textContent.trim().toLowerCase());
    });
    var categoryAt = keys.indexOf("category");
    var countAt = keys.indexOf("fields");

    index.querySelectorAll("tbody tr").forEach(function (tr) {
      var cells = tr.querySelectorAll("td");
      var link = cells.length && cells[0].querySelector("a[href^='#']");
      if (!link) {
        return;
      }
      meta[link.getAttribute("href").slice(1)] = {
        category: categoryAt > -1 ? cells[categoryAt].textContent.trim() : "",
        count: countAt > -1 ? cells[countAt].textContent.trim() : ""
      };
    });
    return meta;
  }

  /* Section anchor -> the field names it contains, read out of the All Fields
     table. This is what lets the rail filter answer "which table has `npi`?"
     rather than only matching table names. */
  function readFieldIndex() {
    var fields = {};
    var all = document.querySelector("table.dd-layout-all");
    if (!all) {
      return fields;
    }
    var keys = [];
    all.querySelectorAll("thead th").forEach(function (th) {
      keys.push(attr(th, "key") || th.textContent.trim().toLowerCase());
    });
    var tableAt = keys.indexOf("table");
    var fieldAt = keys.indexOf("field");
    if (tableAt === -1 || fieldAt === -1) {
      return fields;
    }
    all.querySelectorAll("tbody tr").forEach(function (tr) {
      var cells = tr.querySelectorAll("td");
      if (cells.length <= Math.max(tableAt, fieldAt)) {
        return;
      }
      var href = TABLE_LINKS[cells[tableAt].textContent.trim().toUpperCase()];
      if (!href) {
        return;
      }
      var id = href.slice(1);
      (fields[id] = fields[id] || []).push(
        cells[fieldAt].textContent.trim().toLowerCase()
      );
    });
    return fields;
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

    var hint = document.createElement("span");
    hint.className = "dd-rail-hint";
    hint.hidden = true;

    var count = document.createElement("span");
    count.className = "dd-rail-count";
    count.textContent = item.count;

    element.appendChild(name);
    element.appendChild(hint);
    element.appendChild(count);
    item.hint = hint;
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
    var fieldIndex = readFieldIndex();
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
    filter.setAttribute("aria-label", "Filter tables by name or field name");
    filter.title = "Matches table names and the names of the fields they hold";

    var list = document.createElement("div");
    list.className = "dd-rail-list";
    list.setAttribute("role", "tablist");
    list.setAttribute("aria-orientation", "vertical");

    var empty = document.createElement("p");
    empty.className = "dd-rail-empty";
    empty.textContent = "No table or field matches that filter.";
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

    function activate(id, focus, params) {
      var item = byId[id];
      if (!item) {
        return false;
      }
      selected = item;
      activeSection = id;
      items.forEach(function (other) {
        var on = other === item;
        other.button.setAttribute("aria-selected", on ? "true" : "false");
        other.button.tabIndex = on ? 0 : -1;
        other.section.hidden = !on;
      });
      /* Built on activation rather than up front: a grid needs real layout to
         size its columns, so there is nothing to gain from building one inside
         a hidden panel — and everything to lose. Already built means a return
         visit, which still has to take on any state the URL carries. */
      var built = CONTEXTS[id];
      if (!built) {
        var table = sectionTable(item.section);
        if (table) {
          enhance(table, params);
        }
      } else if (params && params.toString()) {
        applyUrlState(built, params);
      }
      redraw(item.section);
      if (focus) {
        item.button.focus();
      }
      /* The tab that is open owns the query string. A tab arrived at without
         one still has whatever view it was left in, so it describes itself
         rather than claiming a clean slate it does not have. */
      built = CONTEXTS[id];
      writeHash(
        id,
        params && params.toString()
          ? params
          : /* A grid still building has no filters to read yet; its own
               pushUrl will describe it as soon as it does. */
            built && built.ready
            ? serialize(built)
            : new URLSearchParams()
      );
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
        var byName = !needle || item.label.toLowerCase().indexOf(needle) !== -1;
        var matches = 0;
        if (needle && !byName) {
          /* Typing `npi` used to report "no table matches" even though
             PROVIDER holds it. The field names are already on the page, so
             the rail may as well search them. */
          (fieldIndex[item.id] || []).forEach(function (field) {
            if (field.indexOf(needle) !== -1) {
              matches += 1;
            }
          });
        }
        var on = byName || matches > 0;
        item.button.hidden = !on;
        item.hint.hidden = !matches;
        item.hint.textContent = matches
          ? matches + (matches === 1 ? " field" : " fields")
          : "";
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

    window.addEventListener("hashchange", function () {
      var parsed = parseHash();
      /* Links in the index grid point at sections that are now hidden panels,
         so the browser's own jump does nothing and we do it here instead. */
      if (byId[parsed.id] && activate(parsed.id, false, parsed.params)) {
        browser.scrollIntoView({ block: "start" });
      }
    });

    var initial = parseHash();
    var known = byId[initial.id] ? initial.id : null;
    activate(known || items[0].id, false, known ? initial.params : undefined);
    if (known) {
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

    readTableLinks();

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
