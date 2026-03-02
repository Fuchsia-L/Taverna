export const APP_CONFIG = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL || "http://localhost:8000",
  projectTitle: "Taverna",
  theme: {
    defaultTheme: "cyber",
    storageKey: "taverna_theme",
    available: ["cyber", "ember"] as const
  },
  roleLabel: {
    teacher: "老师",
    student: "学生"
  },
  text: {
    initialTeacherMessage: "你好，我是你的老师。请问你今天想学什么？",
    inputPlaceholder: "输入你想提问的内容...",
    sending: "发送中...",
    send: "发送",
    requestFailed: "请求失败，请确认后端服务已启动。",
    sidebarSubtitle: "AI 1v1 Teaching Session",
    sidebarCourseLabel: "当前课程 (Active)",
    securityHint: "SECURE NEURAL CONNECTION ENCRYPTED // AI MAY GENERATE INACCURACIES"
  }
} as const;
