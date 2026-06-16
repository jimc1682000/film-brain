---
title: API 文件
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

# API 文件

後端 FastAPI 的 OpenAPI 規格,由程式自動生成(CI 有 drift gate 確保不過時)。
本機跑起來後也能用互動式 Swagger:`http://localhost:8000/api/docs`。

<div id="redoc"></div>
