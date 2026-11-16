import DefaultTheme from "vitepress/theme";
import Layout from "./Layout.vue";
import SearchReplay from "./SearchReplay.vue";
import EvalChart from "./EvalChart.vue";
import { toRaw } from "vue";
import { cjkTokenize } from "../cjk-tokenize.js";
import "./brand.css";

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ app, siteData }) {
    app.component("SearchReplay", SearchReplay);
    app.component("EvalChart", EvalChart);
    // config.mjs keeps the tokenizer out of the serialized site data (CSP, no
    // eval); restore it here so queries are split the same way as the index.
    toRaw(siteData.value).themeConfig.search.options.miniSearch.options = { tokenize: cjkTokenize };
  },
};
