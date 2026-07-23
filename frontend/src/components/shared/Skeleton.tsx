interface SkeletonProps {
  width?: number | string;
  height?: number;
  borderRadius?: string;
  count?: number;
  style?: React.CSSProperties;
}

export function Skeleton({
  width = "100%",
  height = 16,
  borderRadius = "var(--radius)",
  count = 1,
  style,
}: SkeletonProps) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <div
          key={i}
          className="skeleton"
          style={{
            width: typeof width === "number" ? `${width}px` : width,
            height,
            borderRadius,
            marginBottom: i < count - 1 ? 8 : 0,
            ...style,
          }}
        />
      ))}
    </>
  );
}
