---
title: API reference
aside: false
outline: false
---

<script setup>
import { onMounted } from 'vue'

onMounted(() => {
  const s = document.createElement('script')
  s.src = 'https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js'
  s.onload = () =>
    window.Redoc.init(
      '/film-brain/openapi.json',
      { theme: { colors: { primary: { main: '#f26f21' } } }, hideDownloadButton: false },
      document.getElementById('redoc'),
    )
  document.body.appendChild(s)
})
</script>

# API reference

The backend FastAPI OpenAPI schema, generated from code (a CI drift gate keeps
it current). Running locally, the interactive Swagger UI is at
`http://localhost:8000/api/docs`.

<div id="redoc"></div>
