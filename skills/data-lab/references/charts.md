# Charts and design

Adapted from upstream create-viz/data-visualization. Charts must come from actual computed data.

| Question | Suitable display |
|---|---|
| Change over time | Line chart; preserve missing periods as gaps |
| Category comparison/ranking | Sorted bars or dots |
| Distribution | Histogram, box/violin plot |
| Correlation | Scatter, correlation heatmap with sample context |
| Cohort retention | Heatmap with incomplete observation windows masked |
| Composition | Stacked bars/area; pie only for a few clear parts |
| Model fit | Holdout observed vs predicted, residuals, confusion matrix |

Static PNG and SVG suit reports. Interactive Plotly HTML can provide hover/zoom; optional `plotly` dependency must exist. Bundle JS when a file must work offline. Dashboard helpers use local inline SVG/JS without a CDN.

Use a restrained palette, purposeful highlighting, readable units and conclusion-led titles when evidence supports them. Bar axes include zero; time-series axes can use a justified nonzero range. Consistent panel scales, uncertainty where warranted, and non-color cues improve readability. Avoid misleading 3D or arbitrary dual axes.

Chinese output: prefer available Microsoft YaHei on Windows, PingFang on macOS, or installed open-source Noto Sans CJK/Source Han. Check the actual renderer's available fonts. Installing a font on the desktop does not always make it available in a container. Respect font licenses; do not copy proprietary fonts into the plugin.

Inspect exported charts for glyphs, clipping, labels, missing points and source consistency. The bundled chart exporter records its selected fonts; an available font family alone does not prove every glyph renders.

Keep display precision and visual emphasis consistent. Near-constant series can contain floating-point or source-rounding differences smaller than a displayed unit; do not magnify these into a dramatic spike with identical tick labels. The standard line renderer enforces a minimum value-axis span and tick spacing based on metric precision. When categories share the same displayed value, the automatic narrative discloses a display tie and preserves exact calculations instead of implying a meaningful unique leader.
