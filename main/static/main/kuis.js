/* KUIS Corpus Explorer — progressive enhancement.
   Every behaviour here is optional: with JS off, the forms still submit, the
   corpus panel is open, and all controls remain reachable. */
(function () {
  'use strict';

  /* --- Loading state ----------------------------------------------------
     Server-rendered analyses can take a moment on large selections. Mark the
     page busy on navigation so "working" is never mistaken for "empty". */
  function markBusy() {
    if (document.body.classList.contains('is-loading')) return;
    document.body.classList.add('is-loading');
    document.body.setAttribute('aria-busy', 'true');
    document.querySelectorAll('button[type="submit"]').forEach(function (b) {
      b.disabled = true;
    });
  }

  document.addEventListener('submit', function (e) {
    if (e.target.matches('form') && !e.target.hasAttribute('data-no-loading')) markBusy();
  });

  document.addEventListener('click', function (e) {
    var link = e.target.closest('a[data-loading]');
    if (link && !link.hasAttribute('download') && link.target !== '_blank') markBusy();
  });

  // Back/forward restores a cached page: clear any stale busy state.
  window.addEventListener('pageshow', function () {
    document.body.classList.remove('is-loading');
    document.body.removeAttribute('aria-busy');
    document.querySelectorAll('button[type="submit"]').forEach(function (b) {
      b.disabled = false;
    });
  });

  /* --- Auto-submit controls --------------------------------------------- */
  document.querySelectorAll('[data-autosubmit]').forEach(function (el) {
    el.addEventListener('change', function () {
      if (el.form) el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
    });
  });

  /* --- Context panel (corpus picker) open/close -------------------------- */
  var context = document.querySelector('[data-context]');
  if (context) {
    var toggle = context.querySelector('[data-context-toggle]');
    var panel = context.querySelector('.context__panel');

    if (toggle && panel) {
      // Collapsed once a selection exists; the summary still shows exactly what
      // the analysis is running over. Opens itself when nothing is selected yet.
      setOpen(context.getAttribute('data-start-open') !== 'false', false);

      // Applying a selection reloads the page, so the panel has done its job:
      // close it straight away rather than leaving it over the incoming results.
      context.addEventListener('submit', function () {
        setOpen(false);
      });

      toggle.addEventListener('click', function () {
        setOpen(context.getAttribute('data-open') !== 'true');
      });

      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && context.getAttribute('data-open') === 'true') {
          setOpen(false);
          toggle.focus();
        }
      });
    }

    function setOpen(open, focusSearch) {
      context.setAttribute('data-open', open ? 'true' : 'false');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      toggle.textContent = open ? 'Close' : 'Change selection';
      measureContext();
      if (open && focusSearch !== false) {
        var search = panel.querySelector('[data-picker-search]');
        if (search) search.focus();
      }
    }
  }

  /* --- Theme -------------------------------------------------------------
     Light unless the reader picks dark; the choice is remembered per browser.
     The <head> script has already applied it — this only wires the control. */
  var themeSwitch = document.querySelector('[data-theme-switch]');
  if (themeSwitch) {
    var themeButtons = Array.prototype.slice.call(
      themeSwitch.querySelectorAll('button[data-theme]')
    );

    var applyTheme = function (theme, remember) {
      document.documentElement.setAttribute('data-theme', theme);

      themeButtons.forEach(function (button) {
        button.setAttribute(
          'aria-pressed',
          button.getAttribute('data-theme') === theme ? 'true' : 'false'
        );
      });

      if (remember) {
        try { localStorage.setItem('kuis-theme', theme); } catch (e) { /* ignore */ }
      }
    };

    applyTheme(document.documentElement.getAttribute('data-theme') || 'light', false);
    themeSwitch.hidden = false;

    themeButtons.forEach(function (button) {
      button.addEventListener('click', function () {
        applyTheme(button.getAttribute('data-theme'), true);
      });
    });
  }

  /* --- Sticky offsets ----------------------------------------------------
     Column headers stick below the context bar rather than behind it. */
  function measureContext() {
    var bar = document.querySelector('.context');
    var height = bar ? Math.round(bar.getBoundingClientRect().height) : 0;
    document.documentElement.style.setProperty('--context-h', height + 'px');
  }

  if (document.querySelector('.context')) {
    measureContext();
    window.addEventListener('resize', measureContext);
  }

  /* --- Pickers: search, bulk select, live count --------------------------
     One behaviour, several instances: the analysis corpus picker and each
     column of the assign dialog are all [data-picker]. Anything that changes
     boxes programmatically fires 'kuis:picker-refresh' so the count keeps up. */
  function initPicker(picker) {
    var rows = Array.prototype.slice.call(picker.querySelectorAll('[data-picker-row]'));
    var boxes = rows.map(function (r) { return r.querySelector('input[type="checkbox"]'); });
    var search = picker.querySelector('[data-picker-search]');
    var count = picker.querySelector('[data-picker-count]');
    var dirtyNote = picker.querySelector('[data-picker-dirty]');
    var initial = boxes.map(function (b) { return b.checked; }).join(',');

    function update() {
      var selected = boxes.filter(function (b) { return b.checked; }).length;
      if (count) {
        count.textContent = selected + ' of ' + boxes.length + ' selected';
      }
      if (dirtyNote) {
        var changed = boxes.map(function (b) { return b.checked; }).join(',') !== initial;
        dirtyNote.hidden = !changed;
      }
    }

    picker.addEventListener('change', function (e) {
      if (e.target.matches('input[type="checkbox"]')) update();
    });

    picker.addEventListener('kuis:picker-refresh', update);

    picker.querySelectorAll('[data-picker-all]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var want = btn.getAttribute('data-picker-all') === 'select';
        rows.forEach(function (row, i) {
          if (!row.hidden) boxes[i].checked = want;   // bulk actions respect the filter
        });
        update();
        picker.dispatchEvent(new CustomEvent('kuis:picker-change', { bubbles: true }));
      });
    });

    if (search) {
      search.addEventListener('input', function () {
        var term = search.value.trim().toLowerCase();
        var visible = 0;
        rows.forEach(function (row) {
          var hit = !term || (row.getAttribute('data-search') || '').indexOf(term) !== -1;
          row.hidden = !hit;
          if (hit) visible++;
        });
        var none = picker.querySelector('[data-picker-none]');
        if (none) none.hidden = visible !== 0;
      });
    }

    update();
  }

  document.querySelectorAll('[data-picker]').forEach(initPicker);

  /* --- Assign dialog (files -> corpora) ----------------------------------
     Progressive enhancement: the triggers stay hidden and the plain links do
     the work unless <dialog> is actually usable here. */
  var assign = document.querySelector('[data-assign]');

  if (assign && typeof assign.showModal === 'function') {
    var docPane = assign.querySelector('[data-assign-pane="documents"]');
    var corpusPane = assign.querySelector('[data-assign-pane="corpora"]');
    var submit = assign.querySelector('[data-assign-submit]');
    var summary = assign.querySelector('[data-assign-summary]');
    var lastTrigger = null;

    function checkedIn(pane) {
      return pane
        ? Array.prototype.slice.call(pane.querySelectorAll('input[type="checkbox"]:checked'))
        : [];
    }

    function refreshPane(pane) {
      if (pane) pane.dispatchEvent(new CustomEvent('kuis:picker-refresh'));
    }

    function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

    function updateSummary() {
      var files = checkedIn(docPane).length;
      var corpora = checkedIn(corpusPane).length;
      var ready = files > 0 && corpora > 0;

      if (submit) submit.disabled = !ready;
      if (!summary) return;

      summary.textContent = ready
        ? 'Adding ' + plural(files, 'file', 'files') + ' to ' + plural(corpora, 'corpus', 'corpora') + '.'
        : 'Pick at least one file and one corpus.';
    }

    function setAll(pane, predicate) {
      if (!pane) return;
      pane.querySelectorAll('[data-picker-row]').forEach(function (row) {
        var box = row.querySelector('input[type="checkbox"]');
        if (box) box.checked = predicate(row);
      });
      refreshPane(pane);
    }

    // Selecting the unassigned files is the common case, so it gets its own
    // control — and clears any filter that would hide what it just ticked.
    var unassignedBtn = assign.querySelector('[data-assign-unassigned]');
    if (unassignedBtn) {
      unassignedBtn.addEventListener('click', function () {
        var search = docPane.querySelector('[data-picker-search]');
        if (search && search.value) {
          search.value = '';
          search.dispatchEvent(new Event('input'));
        }
        setAll(docPane, function (row) { return row.hasAttribute('data-unassigned'); });
        updateSummary();
      });
    }

    assign.addEventListener('change', updateSummary);
    assign.addEventListener('kuis:picker-change', updateSummary);

    assign.querySelectorAll('[data-assign-close]').forEach(function (btn) {
      btn.addEventListener('click', function () { assign.close(); });
    });

    // Clicking the backdrop: the dialog itself is the only element that can be
    // the target of a click outside the form.
    assign.addEventListener('click', function (e) {
      if (e.target === assign) assign.close();
    });

    assign.addEventListener('close', function () {
      if (lastTrigger && document.contains(lastTrigger)) lastTrigger.focus();
    });

    document.querySelectorAll('[data-assign-open]').forEach(function (trigger) {
      trigger.hidden = false;

      trigger.addEventListener('click', function () {
        lastTrigger = trigger;

        // Every opening starts from a clean slate, then applies whatever the
        // trigger asked to preselect.
        setAll(docPane, function () { return false; });
        setAll(corpusPane, function () { return false; });

        if (trigger.getAttribute('data-assign-preselect') === 'unassigned') {
          setAll(docPane, function (row) { return row.hasAttribute('data-unassigned'); });
        }

        updateSummary();
        assign.showModal();

        var search = docPane && docPane.querySelector('[data-picker-search]');
        if (search) search.focus();
      });
    });

    // The plain links only exist for the no-dialog path.
    document.querySelectorAll('[data-assign-fallback]').forEach(function (link) {
      link.hidden = true;
    });

    updateSummary();
  }
})();
