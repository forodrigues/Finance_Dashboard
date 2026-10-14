/* Chart helpers shared by the dashboard and investments pages (Chart.js 4). */
(function () {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const eur = new Intl.NumberFormat('pt-PT', { style: 'currency', currency: 'EUR' });
  const eurShort = new Intl.NumberFormat('pt-PT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });

  function baseOptions(extra) {
    Chart.defaults.color = css('--chart-text');
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    const grid = { color: css('--chart-grid') };
    return Object.assign({
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', align: 'end', labels: { boxWidth: 12, boxHeight: 12, useBorderRadius: true, borderRadius: 3 } },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y == null ? '—' : eur.format(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        // Horizontal month labels; Chart.js skips some when space is short (phones).
        x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, autoSkipPadding: 8 } },
        y: { grid, border: { display: false }, ticks: { callback: (v) => eurShort.format(v) } },
      },
    }, extra || {});
  }

  function bars(canvas, labels, datasets) {
    return new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: datasets.map((d) => ({
          borderRadius: 4, borderSkipped: 'start', maxBarThickness: 22,
          borderColor: 'transparent', borderWidth: 1, ...d,
        })),
      },
      options: baseOptions(),
    });
  }

  function line(canvas, labels, dataset, opts) {
    const options = baseOptions();
    options.plugins.legend.display = false;  // single series: the card title names it
    if (opts && opts.percent) {
      options.scales.y.ticks.callback = (v) => `${v} %`;
      options.plugins.tooltip.callbacks.label = (ctx) => ctx.parsed.y == null ? '—' : `${ctx.parsed.y.toLocaleString('pt-PT')} %`;
    }
    return new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          borderWidth: 2, pointRadius: 3, pointHoverRadius: 5, tension: 0.25, spanGaps: true,
          borderColor: css('--series-1'), backgroundColor: css('--series-1'), pointBackgroundColor: css('--series-1'), ...dataset,
        }],
      },
      options,
    });
  }

  function horizontalBars(canvas, labels, values, label) {
    const options = baseOptions({ indexAxis: 'y' });
    options.plugins.legend.display = false;
    options.interaction = { mode: 'nearest', axis: 'y', intersect: false };
    options.plugins.tooltip.callbacks.label = (ctx) => eur.format(ctx.parsed.x);
    options.scales = {
      x: { grid: { color: css('--chart-grid') }, border: { display: false }, ticks: { callback: (v) => eurShort.format(v) } },
      y: { grid: { display: false } },
    };
    return new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{ label, data: values, backgroundColor: css('--series-1'), borderRadius: 4, borderSkipped: 'start', maxBarThickness: 18 }],
      },
      options,
    });
  }

  window.FinanceCharts = { css, bars, line, horizontalBars };
})();
