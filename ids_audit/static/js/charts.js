/* Draws each chart from the data the server puts in its panel (ENG-03 5).
 *
 * The data is read with JSON.parse from the json_script beside the chart,
 * never run as code. Each point's value is used only to draw a length or a
 * position; its label and tooltip are the server's own text, and the
 * tooltip, which ECharts writes as HTML, escapes every text. Nothing here
 * calculates a figure. Colours come from the stylesheet, so the theme
 * changes them.
 */
(function () {
  "use strict";

  function colour(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function draw(element) {
    var points = JSON.parse(document.getElementById(element.dataset.chart).textContent);
    var kind = element.dataset.kind;
    var escape = echarts.format.encodeHTML;
    var chart = echarts.getInstanceByDom(element) || echarts.init(element);
    var data = points.map(function (point) {
      return {
        value: Number(point.value),
        itemStyle: {
          color: point.outside ? colour("--oqgn-orange-text") : colour("--oqgn-green-text"),
        },
      };
    });
    var text = function (item) {
      return points[item.dataIndex].text;
    };
    var option = {
      animation: false,
      grid: { containLabel: true, left: 8, right: 120, top: 12, bottom: 8 },
      tooltip: {
        trigger: "item",
        formatter: function (item) {
          var point = points[item.dataIndex];
          var title = point.name ? point.label + " " + point.name : point.label;
          return escape(title) + "<br>" + escape(point.text) + " OMR";
        },
      },
    };
    var labels = points.map(function (point) {
      return point.label;
    });
    if (kind === "bars") {
      option.xAxis = { type: "value", axisLabel: { show: false } };
      option.yAxis = { type: "category", data: labels, inverse: true };
      option.series = [
        { type: "bar", data: data, label: { show: true, position: "right", formatter: text } },
      ];
    } else {
      option.xAxis = { type: "category", data: labels };
      option.yAxis = { type: "value", axisLabel: { show: false } };
      option.series = [
        kind === "line"
          ? { type: "line", data: data, lineStyle: { color: colour("--oqgn-green-dark") }, itemStyle: { color: colour("--oqgn-green-dark") } }
          : { type: "bar", data: data },
      ];
    }
    chart.setOption(option, true);
    chart.off("click");
    chart.on("click", function (item) {
      var link = points[item.dataIndex].link;
      if (link) {
        window.location.href = link;
      }
    });
  }

  htmx.onLoad(function (root) {
    root.querySelectorAll("[data-chart]").forEach(draw);
  });

  // A swapped-out panel's charts are let go, so none is left behind.
  document.addEventListener("htmx:beforeSwap", function (event) {
    event.detail.target.querySelectorAll("[data-chart]").forEach(function (element) {
      var chart = echarts.getInstanceByDom(element);
      if (chart) {
        chart.dispose();
      }
    });
  });

  window.addEventListener("resize", function () {
    document.querySelectorAll("[data-chart]").forEach(function (element) {
      var chart = echarts.getInstanceByDom(element);
      if (chart) {
        chart.resize();
      }
    });
  });
})();
