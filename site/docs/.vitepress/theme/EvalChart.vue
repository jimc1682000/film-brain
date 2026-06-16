<script setup>
import { ref, onMounted } from "vue";

const canvas = ref(null);

onMounted(async () => {
  const { default: Chart } = await import("chart.js/auto");
  const labels = ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"];
  const ndcg = [0.9307, 0.9255, 0.9512, 0.9368, 0.935, 0.9623, 0.9368, 0.9625];
  const milestones = { 2: "0.9512", 5: "0.9623 · MRR 1.0", 7: "0.9625 ⭐ P@5+0.10" };

  new Chart(canvas.value, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "nDCG@5",
          data: ndcg,
          borderColor: "#f26f21",
          backgroundColor: "#f26f21",
          tension: 0.25,
          pointRadius: ndcg.map((_, i) => (i in milestones ? 6 : 3)),
          pointBackgroundColor: ndcg.map((_, i) => (i in milestones ? "#ffb380" : "#f26f21")),
        },
        {
          label: "v1 baseline (0.9307)",
          data: ndcg.map(() => 0.9307),
          borderColor: "#777",
          borderDash: [6, 6],
          borderWidth: 1,
          pointRadius: 0,
        },
      ],
    },
    options: {
      animation: false,
      scales: {
        y: { min: 0.92, max: 0.97, ticks: { color: "#999" }, grid: { color: "#333" } },
        x: { ticks: { color: "#999" }, grid: { color: "#333" } },
      },
      plugins: {
        legend: { labels: { color: "#ccc" } },
        tooltip: {
          callbacks: { afterLabel: (c) => milestones[c.dataIndex] || "" },
        },
      },
    },
  });
});
</script>

<template>
  <div style="background:#121212;border:1px solid #2a2a2a;border-radius:10px;padding:18px;margin:18px 0">
    <canvas ref="canvas" height="120"></canvas>
  </div>
</template>
