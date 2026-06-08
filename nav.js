/* Collapse the header nav into a hamburger menu. Works on every page by
   transforming the existing <header class="site"><nav> at load time, so no
   per-page markup change is needed. */
document.addEventListener("DOMContentLoaded", () => {
  const header = document.querySelector("header.site");
  if (!header) return;
  const nav = header.querySelector("nav");
  if (!nav) return;

  const btn = document.createElement("button");
  btn.className = "navtoggle";
  btn.setAttribute("aria-label", "選單 / Menu");
  btn.setAttribute("aria-expanded", "false");
  btn.textContent = "☰";
  header.appendChild(btn);

  const close = () => { nav.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); };
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = nav.classList.toggle("open");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  });
  // Click outside, or pick a link, closes the menu.
  document.addEventListener("click", close);
  nav.addEventListener("click", (e) => {
    if (e.target.tagName === "A") close();
    else e.stopPropagation();
  });
});
