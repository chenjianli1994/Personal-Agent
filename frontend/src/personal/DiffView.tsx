export function DiffView({
  text,
  className = "artifact-diff-text diff-view",
}: {
  text: string;
  className?: string;
}) {
  return (
    <pre className={className}>
      {text.split("\n").map((line, index) => {
        const variant = line.startsWith("+") && !line.startsWith("+++")
          ? "add"
          : line.startsWith("-") && !line.startsWith("---")
            ? "del"
            : line.startsWith("@@")
              ? "hunk"
              : "";
        return (
          <span key={`${index}-${line}`} className={`diff-line${variant ? ` ${variant}` : ""}`}>
            {line || " "}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}
