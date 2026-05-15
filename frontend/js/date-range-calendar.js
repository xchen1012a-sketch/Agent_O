/**
 * Apple 风格日期范围选择（原生 JS，无依赖）
 * mountDateRangeCalendar(container, { initialDateFrom, initialDateTo, onRangeComplete })
 */
(function (global) {
  'use strict';

  function pad2(n) {
    return (n < 10 ? '0' : '') + n;
  }

  function ymdLocal(d) {
    if (!d || !(d instanceof Date) || isNaN(d.getTime())) return '';
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }

  function parseYmdLocal(s) {
    if (!s || typeof s !== 'string') return null;
    var p = s.split('-');
    if (p.length < 3) return null;
    var y = parseInt(p[0], 10);
    var m = parseInt(p[1], 10) - 1;
    var day = parseInt(p[2], 10);
    var dt = new Date(y, m, day);
    if (isNaN(dt.getTime())) return null;
    if (dt.getFullYear() !== y || dt.getMonth() !== m || dt.getDate() !== day) return null;
    return dt;
  }

  var WEEK_HEADERS = ['日', '一', '二', '三', '四', '五', '六'];

  function mountDateRangeCalendar(container, options) {
    if (!container) return;
    options = options || {};
    var onRangeComplete =
      typeof options.onRangeComplete === 'function' ? options.onRangeComplete : function () {};

    var initialFrom = parseYmdLocal(options.initialDateFrom);
    var initialTo = parseYmdLocal(options.initialDateTo);

    var view = new Date();
    if (initialTo) {
      view = new Date(initialTo.getFullYear(), initialTo.getMonth(), 1);
    } else if (initialFrom) {
      view = new Date(initialFrom.getFullYear(), initialFrom.getMonth(), 1);
    }

    var phase = 0;
    var selA = null;

    function monthLabel(y, m) {
      return y + ' 年 ' + (m + 1) + ' 月';
    }

    function render() {
      var y = view.getFullYear();
      var mo = view.getMonth();
      var first = new Date(y, mo, 1);
      var startPad = first.getDay();
      var dim = new Date(y, mo + 1, 0).getDate();
      var todayStr = ymdLocal(new Date());

      var displayStart = null;
      var displayEnd = null;
      if (phase === 1 && selA) {
        displayStart = selA;
        displayEnd = selA;
      } else if (phase === 0 && initialFrom && initialTo) {
        displayStart = initialFrom;
        displayEnd = initialTo;
      }

      var ds = displayStart ? ymdLocal(displayStart) : '';
      var de = displayEnd ? ymdLocal(displayEnd) : '';

      var cells = [];
      var totalCells = Math.ceil((startPad + dim) / 7) * 7;
      for (var i = 0; i < totalCells; i++) {
        var dayNum = i - startPad + 1;
        if (i < startPad || dayNum > dim) {
          cells.push('<div class="h-9 w-9" aria-hidden="true"></div>');
          continue;
        }
        var cellDate = new Date(y, mo, dayNum);
        var ymd = ymdLocal(cellDate);
        var isToday = ymd === todayStr;
        var inRange = !!(ds && de && ymd >= ds && ymd <= de);
        var isEndpoint = inRange && (ymd === ds || ymd === de);
        var isMid = inRange && !isEndpoint;

        var cls =
          'dash-cal-day flex h-9 min-w-[2.25rem] items-center justify-center rounded-lg text-sm font-medium transition-colors duration-150 ';
        if (isEndpoint) {
          cls += 'bg-[#007AFF] text-white shadow-sm';
        } else if (isMid) {
          cls += 'bg-blue-500/15 text-gray-900';
        } else {
          cls += 'text-gray-700 hover:bg-gray-100';
        }
        if (isToday && !inRange) {
          cls += ' ring-2 ring-[#007AFF]/25';
        }

        cells.push(
          '<button type="button" class="' +
            cls +
            '" data-ymd="' +
            ymd +
            '" aria-pressed="' +
            (inRange ? 'true' : 'false') +
            '">' +
            dayNum +
            '</button>'
        );
      }

      var head = WEEK_HEADERS.map(function (w) {
        return '<div class="flex h-7 items-center justify-center text-[10px] font-medium text-gray-400">' + w + '</div>';
      }).join('');

      container.innerHTML =
        '<div class="select-none">' +
          '<div class="mb-2 flex items-center justify-between gap-2">' +
            '<button type="button" class="dash-cal-prev flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-gray-600 transition-colors hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#007AFF]/35" aria-label="上一月">' +
              '<svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7"/></svg>' +
            '</button>' +
            '<span class="min-w-0 flex-1 text-center text-sm font-semibold tabular-nums text-gray-900">' +
              monthLabel(y, mo) +
            '</span>' +
            '<button type="button" class="dash-cal-next flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-gray-600 transition-colors hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#007AFF]/35" aria-label="下一月">' +
              '<svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>' +
            '</button>' +
          '</div>' +
          '<div class="grid grid-cols-7 gap-0.5">' +
            head +
          '</div>' +
          '<div class="mt-1 grid grid-cols-7 gap-0.5">' +
            cells.join('') +
          '</div>' +
          '<p class="mt-3 text-center text-[11px] leading-relaxed text-gray-400">点选第一天与最后一天以设定区间</p>' +
        '</div>';

      var prev = container.querySelector('.dash-cal-prev');
      var next = container.querySelector('.dash-cal-next');
      if (prev) {
        prev.onclick = function (e) {
          e.preventDefault();
          e.stopPropagation();
          view = new Date(y, mo - 1, 1);
          render();
        };
      }
      if (next) {
        next.onclick = function (e) {
          e.preventDefault();
          e.stopPropagation();
          view = new Date(y, mo + 1, 1);
          render();
        };
      }

      var buttons = container.querySelectorAll('.dash-cal-day');
      for (var bi = 0; bi < buttons.length; bi++) {
        buttons[bi].onclick = function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          var ymdClick = this.getAttribute('data-ymd');
          var d = parseYmdLocal(ymdClick);
          if (!d) return;

          if (phase === 0) {
            selA = d;
            phase = 1;
            render();
            return;
          }

          var a = selA;
          var b = d;
          if (ymdLocal(b) < ymdLocal(a)) {
            var t = a;
            a = b;
            b = t;
          }
          var fromStr = ymdLocal(a);
          var toStr = ymdLocal(b);
          initialFrom = a;
          initialTo = b;
          phase = 0;
          selA = null;
          onRangeComplete(fromStr, toStr);
          render();
        };
      }
    }

    render();
  }

  global.mountDateRangeCalendar = mountDateRangeCalendar;
})(typeof window !== 'undefined' ? window : this);
