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
      // Collapsed by default once JS is available; the summary still shows
      // exactly what the analysis is running over.
      var startOpen = context.getAttribute('data-open') === 'true';
      setOpen(startOpen);

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

    function setOpen(open) {
      context.setAttribute('data-open', open ? 'true' : 'false');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      toggle.textContent = open ? 'Close' : 'Change selection';
      measureContext();
      if (open) {
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

  /* --- Corpus picker: search, bulk select, live count -------------------- */
  var picker = document.querySelector('[data-picker]');
  if (picker) {
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

    picker.querySelectorAll('[data-picker-all]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var want = btn.getAttribute('data-picker-all') === 'select';
        rows.forEach(function (row, i) {
          if (!row.hidden) boxes[i].checked = want;   // bulk actions respect the filter
        });
        update();
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
})();
