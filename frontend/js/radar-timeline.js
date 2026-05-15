(function () {
  'use strict';

  var AgentO = window.AgentO = window.AgentO || {};

  var DEFAULT_DIMENSIONS = [
    { key: 'product_knowledge', label: '产品知识' },
    { key: 'compliance_expression', label: '合规表达' },
    { key: 'needs_discovery', label: '需求挖掘' },
    { key: 'sales_expression', label: '销售沟通' },
    { key: 'objection_handling', label: '异议处理' },
    { key: 'closing_skill', label: '成交收口' },
  ];

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function clampScore(value) {
    var num = Number(value);
    if (!Number.isFinite(num)) return 0;
    return Math.max(0, Math.min(100, Math.round(num * 10) / 10));
  }

  function formatScore(value) {
    return String(clampScore(value).toFixed(1)).replace(/\.0$/, '');
  }

  function normalizeDimensions(raw) {
    var source = Array.isArray(raw) && raw.length ? raw : DEFAULT_DIMENSIONS;
    return source.map(function (item, index) {
      item = item || {};
      var fallback = DEFAULT_DIMENSIONS[index] || DEFAULT_DIMENSIONS[0];
      return {
        key: String(item.key || fallback.key),
        label: String(item.label || fallback.label),
      };
    }).slice(0, 8);
  }

  function valueFromItem(raw, key) {
    if (!raw || typeof raw !== 'object') return 0;
    var values = raw.values && typeof raw.values === 'object' ? raw.values : {};
    if (values[key] != null) return values[key];
    if (raw[key] != null) return raw[key];
    return raw.overall_score != null ? raw.overall_score : raw.overallScore;
  }

  function normalizeRadarTimelineItems(rawItems, rawDimensions) {
    var dimensions = normalizeDimensions(rawDimensions);
    if (!Array.isArray(rawItems)) return [];
    return rawItems.map(function (raw, index) {
      raw = raw || {};
      var valueMap = {};
      var seriesValues = dimensions.map(function (dim) {
        var score = clampScore(valueFromItem(raw, dim.key));
        valueMap[dim.key] = score;
        return score;
      });
      var label = String(raw.label || raw.title || ('记录 ' + (index + 1))).trim();
      return {
        label: label,
        createdAt: String(raw.created_at || raw.createdAt || ''),
        stageNo: Number(raw.stage_no || raw.stageNo || 0) || 0,
        dayIndex: Number(raw.day_index || raw.dayIndex || raw.cycle_day_index || 0) || 0,
        overallScore: clampScore(raw.overall_score != null ? raw.overall_score : raw.overallScore),
        moduleName: String(raw.module_name || raw.moduleName || ''),
        summary: String(raw.summary || ''),
        values: valueMap,
        seriesValues: seriesValues,
      };
    });
  }

  function prefersReducedMotion() {
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) {
      return false;
    }
  }

  function itemTone(item, first) {
    var delta = item && first ? item.overallScore - first.overallScore : 0;
    if (delta >= 10) return '#0f766e';
    if (delta >= 0) return '#2563eb';
    return '#b45309';
  }

  function buildRadarTimelineOption(data) {
    data = data || {};
    var dimensions = normalizeDimensions(data.dimensions);
    var items = normalizeRadarTimelineItems(data.items || [], dimensions);
    var reduced = prefersReducedMotion();
    var first = items[0] || null;
    var indicators = dimensions.map(function (dim) {
      return { name: dim.label, max: 100 };
    });
    return {
      baseOption: {
        animation: !reduced,
        animationDuration: reduced ? 0 : 420,
        animationDurationUpdate: reduced ? 0 : 620,
        animationEasingUpdate: 'cubicOut',
        color: ['#0f766e', '#2563eb'],
        timeline: {
          axisType: 'category',
          autoPlay: !reduced && items.length > 1,
          playInterval: 1250,
          loop: false,
          bottom: 4,
          left: 26,
          right: 26,
          height: 64,
          symbol: 'circle',
          symbolSize: 9,
          currentIndex: reduced ? Math.max(0, items.length - 1) : 0,
          checkpointStyle: {
            color: '#0f766e',
            borderColor: '#ccfbf1',
            borderWidth: 2,
          },
          controlStyle: {
            color: '#0f766e',
            borderColor: '#0f766e',
            itemSize: 18,
          },
          label: {
            color: '#64748b',
            fontSize: 11,
            lineHeight: 14,
            formatter: function (value) {
              return String(value || '').replace(/\s+·\s+/g, '\n');
            },
          },
          lineStyle: {
            color: '#cbd5e1',
          },
          data: items.map(function (item) { return item.label; }),
        },
        tooltip: {
          trigger: 'item',
          confine: true,
          formatter: function (params) {
            var values = params && params.value ? params.value : [];
            var rows = dimensions.map(function (dim, index) {
              return escapeHtml(dim.label) + '：' + escapeHtml(formatScore(values[index] || 0)) + ' 分';
            });
            return '<strong>' + escapeHtml(params.name || '') + '</strong><br />' + rows.join('<br />');
          },
        },
        radar: {
          center: ['50%', '42%'],
          radius: '62%',
          splitNumber: 4,
          indicator: indicators,
          axisName: {
            color: '#334155',
            fontSize: 12,
            lineHeight: 16,
          },
          splitLine: {
            lineStyle: {
              color: ['#e2e8f0'],
            },
          },
          splitArea: {
            areaStyle: {
              color: ['rgba(248,250,252,0.88)', 'rgba(240,253,250,0.76)'],
            },
          },
          axisLine: {
            lineStyle: {
              color: '#cbd5e1',
            },
          },
        },
        series: [
          {
            type: 'radar',
            symbol: 'circle',
            symbolSize: 5,
            lineStyle: {
              width: 3,
            },
            areaStyle: {
              opacity: 0.2,
            },
            emphasis: {
              lineStyle: { width: 4 },
            },
            data: [],
          },
        ],
      },
      options: items.map(function (item) {
        var tone = itemTone(item, first);
        return {
          series: [
            {
              lineStyle: { color: tone },
              areaStyle: { color: tone, opacity: 0.2 },
              data: [
                {
                  value: item.seriesValues,
                  name: item.label,
                  itemStyle: { color: tone },
                  areaStyle: { color: tone, opacity: 0.2 },
                },
              ],
            },
          ],
        };
      }),
    };
  }

  function scoreDeltaText(first, item) {
    var delta = item && first ? Math.round((item.overallScore - first.overallScore) * 10) / 10 : 0;
    return (delta > 0 ? '+' : '') + String(delta).replace(/\.0$/, '');
  }

  function screenSummaryFor(items, index) {
    if (!items.length) return '暂无能力轨迹。';
    var first = items[0];
    var item = items[Math.max(0, Math.min(items.length - 1, index || 0))] || first;
    return '当前节点 ' + item.label + '，综合分 ' + formatScore(item.overallScore) + '，较起点变化 ' + scoreDeltaText(first, item) + ' 分。';
  }

  function updateCurrentMeta(container, items, index) {
    if (!container || !items.length || !container.querySelector) return;
    var item = items[Math.max(0, Math.min(items.length - 1, index || 0))] || items[0];
    var first = items[0];
    var moduleName = item.moduleName || '综合能力';
    var summary = screenSummaryFor(items, index);
    var currentScoreEl = container.querySelector('[data-radar-timeline-current-score]');
    var deltaEl = container.querySelector('[data-radar-timeline-delta]');
    var labelEl = container.querySelector('[data-radar-timeline-current-label]');
    var moduleEl = container.querySelector('[data-radar-timeline-current-module]');
    var a11yEl = container.querySelector('[data-radar-timeline-a11y]');
    var chartEl = container.querySelector('[data-radar-timeline-chart]');
    if (currentScoreEl) currentScoreEl.textContent = formatScore(item.overallScore);
    if (deltaEl) deltaEl.textContent = scoreDeltaText(first, item);
    if (labelEl) labelEl.textContent = item.label || '--';
    if (moduleEl) moduleEl.textContent = moduleName;
    if (a11yEl) a11yEl.textContent = summary;
    if (chartEl && chartEl.setAttribute) chartEl.setAttribute('aria-label', summary);
  }

  function renderFrame(container, props, items) {
    props = props || {};
    var first = items[0];
    var last = items[items.length - 1];
    var userName = String(props.userName || props.selectedUserName || '').trim();
    var subtitle = userName ? ('当前查看：' + userName) : '跟踪阶段训练后的六维能力变化';
    var latestModule = last && last.moduleName ? last.moduleName : '综合能力';
    var screenSummary = screenSummaryFor(items, items.length - 1);

    container.innerHTML =
      '<div class="radar-timeline-shell">' +
        '<div class="radar-timeline-head">' +
          '<div>' +
            '<div class="radar-timeline-kicker">能力跃迁</div>' +
            '<h2 class="radar-timeline-title">雷达图跃迁</h2>' +
            '<p class="radar-timeline-subtitle">' + escapeHtml(subtitle) + '</p>' +
          '</div>' +
          '<div class="radar-timeline-stats" aria-label="能力跃迁摘要">' +
            '<div><span>起点</span><strong>' + escapeHtml(first ? formatScore(first.overallScore) : '--') + '</strong></div>' +
            '<div><span>当前</span><strong data-radar-timeline-current-score>' + escapeHtml(last ? formatScore(last.overallScore) : '--') + '</strong></div>' +
            '<div><span>跃迁</span><strong data-radar-timeline-delta>' + escapeHtml(scoreDeltaText(first, last)) + '</strong></div>' +
          '</div>' +
        '</div>' +
        '<div class="radar-timeline-body">' +
          '<div class="radar-timeline-chart" data-radar-timeline-chart role="img" aria-label="' + escapeHtml(screenSummary) + '"></div>' +
          '<aside class="radar-timeline-current" aria-label="当前能力节点">' +
            '<span>当前节点</span>' +
            '<strong data-radar-timeline-current-label>' + escapeHtml(last ? last.label : '--') + '</strong>' +
            '<p data-radar-timeline-current-module>' + escapeHtml(latestModule) + '</p>' +
          '</aside>' +
        '</div>' +
        '<p class="radar-timeline-a11y" data-radar-timeline-a11y aria-live="polite">' + escapeHtml(screenSummary) + '</p>' +
      '</div>';
  }

  function renderLoading(container, props) {
    var userName = String((props && (props.userName || props.selectedUserName)) || '').trim();
    container.innerHTML =
      '<div class="radar-timeline-shell radar-timeline-shell--loading">' +
        '<div class="radar-timeline-head">' +
          '<div><div class="radar-timeline-kicker">能力跃迁</div><h2 class="radar-timeline-title">雷达图跃迁</h2><p class="radar-timeline-subtitle">' + escapeHtml(userName ? ('当前查看：' + userName) : '正在读取能力轨迹') + '</p></div>' +
        '</div>' +
        '<div class="radar-timeline-skeleton" aria-live="polite">加载能力轨迹中...</div>' +
      '</div>';
  }

  function renderEmpty(container, message) {
    container.innerHTML =
      '<div class="radar-timeline-shell radar-timeline-shell--empty">' +
        '<div class="radar-timeline-head">' +
          '<div><div class="radar-timeline-kicker">能力跃迁</div><h2 class="radar-timeline-title">雷达图跃迁</h2></div>' +
        '</div>' +
        '<div class="radar-timeline-empty">' + escapeHtml(message || '暂无能力轨迹') + '</div>' +
      '</div>';
    return { items: [], option: null, chart: null };
  }

  function initChart(container, chartEl, option, items) {
    if (!chartEl || !window.echarts || typeof window.echarts.init !== 'function') return null;
    if (container.__radarTimelineChart && typeof container.__radarTimelineChart.dispose === 'function') {
      container.__radarTimelineChart.dispose();
    }
    var chart = window.echarts.init(chartEl);
    chart.setOption(option, true);
    if (chart.on) {
      chart.on('timelinechanged', function (params) {
        updateCurrentMeta(container, items, params && Number.isFinite(Number(params.currentIndex)) ? Number(params.currentIndex) : 0);
      });
    }
    container.__radarTimelineChart = chart;
    if (!container.__radarTimelineResizeBound && window.addEventListener) {
      window.addEventListener('resize', function () {
        if (container.__radarTimelineChart && typeof container.__radarTimelineChart.resize === 'function') {
          container.__radarTimelineChart.resize();
        }
      });
      container.__radarTimelineResizeBound = true;
    }
    return chart;
  }

  function renderWithData(container, data, props) {
    props = props || {};
    data = data || {};
    var dimensions = normalizeDimensions(data.dimensions || props.dimensions);
    var items = normalizeRadarTimelineItems(data.items || [], dimensions);
    if (!items.length) return renderEmpty(container, '暂无能力轨迹');
    renderFrame(container, props, items);

    var option = buildRadarTimelineOption({ items: items, dimensions: dimensions });
    var chartEl = container.querySelector ? container.querySelector('[data-radar-timeline-chart]') : null;
    var chart = initChart(container, chartEl, option, items);
    updateCurrentMeta(container, items, prefersReducedMotion() ? items.length - 1 : 0);
    return { items: items, option: option, chart: chart };
  }

  function renderRadarTimeline(container, props) {
    if (!container) return null;
    if (Array.isArray(props)) {
      return renderWithData(container, { items: props }, {});
    }
    props = props || {};
    if (Array.isArray(props.items)) {
      return renderWithData(container, { items: props.items, dimensions: props.dimensions }, props);
    }
    if (props.data && Array.isArray(props.data.items)) {
      return renderWithData(container, props.data, props);
    }
    if (typeof props.apiFetch === 'function') {
      var requestKey = String(props.userId || 'self') + ':' + String(Date.now());
      if (container.dataset) container.dataset.radarTimelineRequest = requestKey;
      renderLoading(container, props);
      props.apiFetch({ userId: props.userId }).then(function (data) {
        if (container.dataset && container.dataset.radarTimelineRequest !== requestKey) return;
        renderWithData(container, data || {}, {
          userName: (data && data.selected_user_name) || props.userName,
          dimensions: (data && data.dimensions) || props.dimensions,
        });
      }).catch(function (err) {
        if (container.dataset && container.dataset.radarTimelineRequest !== requestKey) return;
        renderEmpty(container, (err && err.message) || '能力轨迹加载失败');
      });
      return { items: [], option: null, chart: null, pending: true };
    }
    return renderEmpty(container, '暂无能力轨迹');
  }

  AgentO.radarTimelineDimensions = DEFAULT_DIMENSIONS;
  AgentO.normalizeRadarTimelineItems = normalizeRadarTimelineItems;
  AgentO.buildRadarTimelineOption = buildRadarTimelineOption;
  AgentO.renderRadarTimeline = renderRadarTimeline;
})();
