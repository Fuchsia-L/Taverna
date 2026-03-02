# Frontend (React + Vite + TypeScript)

## 技术栈

- React + Vite + TypeScript
- Tailwind CSS
- shadcn/ui 风格基础组件（已包含 `Button` 与 `components.json`）

## 目录结构

```text
src/
  config/
  components/
    chat/
    ui/
  pages/
  types/
  styles/
  services/
```

## 启动方式

1. 安装依赖

```bash
npm install
```

2. 启动开发服务器

```bash
npm run dev
```

默认地址：`http://localhost:5173`

前端会调用后端接口：`http://localhost:8000/api/chat`


## 环境变量

- VITE_API_BASE_URL：后端地址（默认 http://localhost:8000）

## 主题机制

- 主题令牌在 `src/styles/globals.css` 的 `[data-theme="..."]` 变量中定义
- Tailwind 通过 `tailwind.config.js` 的语义色 `app.*` 读取这些变量
- 默认主题在 `src/config/app.ts` 的 `theme.defaultTheme` 配置
