import React from "react";
import ReactDOM from "react-dom/client";
import "katex/dist/katex.min.css";
import App from "./App";
import "./styles/globals.css";
import { APP_CONFIG } from "@/config/app";
import { SettingsProvider } from "@/stores/settingsStore";

const storedTheme = localStorage.getItem(APP_CONFIG.theme.storageKey);
const initialTheme =
  storedTheme && APP_CONFIG.theme.available.includes(storedTheme as (typeof APP_CONFIG.theme.available)[number])
    ? storedTheme
    : APP_CONFIG.theme.defaultTheme;
document.documentElement.dataset.theme = initialTheme;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SettingsProvider>
      <App />
    </SettingsProvider>
  </React.StrictMode>
);
