import { Component, type ErrorInfo, type ReactNode } from "react";

export class WidgetErrorBoundary extends Component<
  { children: ReactNode; widget?: string; binding?: string },
  { error?: Error; correlationId?: string }
> {
  state: { error?: Error; correlationId?: string } = {};

  static getDerivedStateFromError(error: Error) {
    return { error, correlationId: crypto.randomUUID?.() ?? String(Date.now()) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.warn("widget render failed", {
      widget: this.props.widget,
      binding: this.props.binding,
      correlationId: this.state.correlationId,
      error: error.message,
      componentStack: info.componentStack,
    });
  }

  render() {
    if (this.state.error) {
      return (
        <div className="widget-error" role="alert">
          <b>Виджет {this.props.widget ?? "unknown"} не отрисован</b>
          <div className="hint">binding: {this.props.binding || "—"}</div>
          <div className="hint">correlation: {this.state.correlationId}</div>
          <button onClick={() => this.setState({ error: undefined, correlationId: undefined })}>[повторить]</button>
        </div>
      );
    }
    return this.props.children;
  }
}
