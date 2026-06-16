---
title: API reference
layout: page
---

<script setup>
import { onMounted } from 'vue'

// Redoc ships its own (light) theme; pass a dark theme so it doesn't clash with
// the VitePress dark site. layout: page gives it full width.
const REDOC_DARK = {
  hideDownloadButton: false,
  theme: {
    colors: {
      primary: { main: '#f26f21' },
      text: { primary: '#e6e6e6', secondary: '#a8a8a8' },
      border: { dark: '#2a2a2f', light: '#2a2a2f' },
    },
    sidebar: { backgroundColor: '#161618', textColor: '#e6e6e6', activeTextColor: '#f26f21' },
    rightPanel: { backgroundColor: '#0d0d0f', textColor: '#e6e6e6' },
    schema: { nestedBackground: '#1b1b1f', typeNameColor: '#a8a8a8', typeTitleColor: '#e6e6e6' },
    codeBlock: { backgroundColor: '#1b1b1f' },
    typography: {
      fontFamily: 'inherit',
      headings: { fontFamily: 'inherit' },
      code: { color: '#ffb380', backgroundColor: '#2a2a2a' },
    },
  },
}

onMounted(() => {
  const s = document.createElement('script')
  s.src = 'https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js'
  s.onload = () =>
    window.Redoc.init('/film-brain/openapi.json', REDOC_DARK, document.getElementById('redoc'))
  document.body.appendChild(s)
})
</script>

<div style="max-width: 1152px; margin: 0 auto; padding: 24px 24px 0;">

# API reference

The backend FastAPI OpenAPI schema, generated from code (a CI drift gate keeps
it current). Running locally, the interactive Swagger UI is at
`http://localhost:8000/api/docs`.

</div>

<div id="redoc"></div>
