import DefaultTheme from "vitepress/theme";
import Layout from "./Layout.vue";
import SearchReplay from "./SearchReplay.vue";
import EvalChart from "./EvalChart.vue";
import "./brand.css";

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ app }) {
    app.component("SearchReplay", SearchReplay);
    app.component("EvalChart", EvalChart);
  },
};
