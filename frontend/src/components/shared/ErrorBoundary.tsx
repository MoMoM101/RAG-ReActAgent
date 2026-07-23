import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div style={{ padding: 40, textAlign: "center" }}>
          <h3 style={{ color: "var(--danger)", marginBottom: 8 }}>
            页面出错了
          </h3>
          <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 16 }}>
            {this.state.error?.message || "未知错误"}
          </p>
          <button
            className="btn btn-primary"
            onClick={this.handleReset}
          >
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
