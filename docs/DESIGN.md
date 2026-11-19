# Interface decisions

The requested surface is a black background, named charts and top navigation tabs. The dashboard uses plain typography, a fixed spacing system, thin separators, muted price lines, four grid labels and hover detail. Forms, research assumptions and account reads live in the relevant tabs. There are no decorative hero sections, gradients, gauges or invented market figures.

The design research supports the decisions rather than promising a universal definition of “not AI-looking”:

- [NNGroup: Clutter-Free charts](https://www.nngroup.com/articles/clutter-charts/) — remove visual elements that compete with the chart's point. Applied through restrained gridlines, no chart fills and a small number of labels.
- [NNGroup: Visual hierarchy](https://www.nngroup.com/articles/visual-hierarchy-ux-definition/) — use size, grouping and contrast to guide attention. Applied through ticker/price headers, quieter company names, and a single top tab bar.
- [NNGroup: Complex application guidelines](https://www.nngroup.com/articles/complex-application-design/) — keep functionality discoverable without forcing every detail into the initial view. Applied through tabs, expandable controls, chart enlargement and hover values.

The app is custom HTML/CSS/JavaScript served by Python. It has no remote font, chart CDN or analytics dependency. Canvas rendering keeps the default screen focused on data, with accessible chart names, keyboard-focusable controls, real labels, readable tables and an enlarged chart dialog. Canvas does not expose every data point to screen readers; a complete accessible data-table view is a future improvement.

Three chart columns on normal desktop screens, two on narrower desktop screens, one on phones. Forms and results similarly collapse into single columns. The design avoids making decorative panels look like controls; buttons and links carry explicit interaction styling.

The synthetic badge remains visible on the chart screen, and synthetic result headers repeat that status. Market-return colors represent direction, not confidence or strategy quality. The normalized SPY reference is muted and explicitly described as a price reference, without claiming matched capital exposure or dividends.
