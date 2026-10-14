// Light/dark theme toggle. index.html sets the initial theme before first paint; this keeps it in sync.
const KEY = "dataorigin-theme";

export const currentTheme = () => document.documentElement.getAttribute("data-theme") || "light";

function label(button) {
  const next = currentTheme() === "dark" ? "light" : "dark";
  button.setAttribute("aria-label", `Switch to ${next} theme`);
  button.title = `Switch to ${next} theme`;
}

export function setTheme(theme) {
  const root = document.documentElement;
  root.classList.add("theme-anim"); // crossfade colours only while switching
  root.setAttribute("data-theme", theme);
  try { localStorage.setItem(KEY, theme); } catch { /* private mode: theme just isn't remembered */ }
  setTimeout(() => root.classList.remove("theme-anim"), 600);
}

export function initThemeToggle(button) {
  if (!button) return;
  label(button);
  button.addEventListener("click", () => {
    setTheme(currentTheme() === "dark" ? "light" : "dark");
    label(button);
  });
}
