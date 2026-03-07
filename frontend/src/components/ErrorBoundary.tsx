import React from "react";

interface ErrorBoundaryState {
  hasError: boolean;
  errorMessage: string;
}

export class ErrorBoundary extends React.Component<React.PropsWithChildren, ErrorBoundaryState> {
  state: ErrorBoundaryState = {
    hasError: false,
    errorMessage: "",
  };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    const message = error instanceof Error ? error.message : String(error);
    return {
      hasError: true,
      errorMessage: message,
    };
  }

  componentDidCatch(error: unknown, errorInfo: React.ErrorInfo) {
    // Keep stack in console for debugging.
    console.error("UI crashed:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="flex min-h-screen items-center justify-center bg-app-bg px-6 text-app-text">
          <div className="w-full max-w-2xl rounded-xl border border-app-border/30 bg-app-surface/90 p-6">
            <h1 className="text-lg font-semibold text-app-info">界面发生错误</h1>
            <p className="mt-2 text-sm text-app-muted">
              为避免黑屏，页面已被错误边界接管。请刷新页面后重试。
            </p>
            <pre className="mt-4 max-h-56 overflow-auto rounded-md border border-app-border/25 bg-app-bg/60 p-3 font-mono text-xs text-app-text whitespace-pre-wrap">
              {this.state.errorMessage || "Unknown error"}
            </pre>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-4 rounded-md border border-app-info/40 bg-app-info/15 px-3 py-2 text-sm text-app-info hover:bg-app-info/25"
            >
              刷新页面
            </button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}

