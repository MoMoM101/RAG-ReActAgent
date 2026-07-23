import { useState } from "react";

interface Source {
  citation_id?: string;
  document_id: string;
  document_key?: string;
  section_key?: string;
  filename?: string;
  text: string;
  score?: number;
  rank?: number;
}

export function SourceCard({ sources }: { sources: Source[] }) {
  const [activeIdx, setActiveIdx] = useState<number | null>(null);

  return (
    <div className="source-row">
      {sources.map((s, i) => (
        <button
          key={i}
          className={`source-chip ${activeIdx === i ? "active" : ""}`}
          onClick={() => setActiveIdx(activeIdx === i ? null : i)}
          title={s.text.slice(0, 300)}
        >
          {activeIdx === i
            ? s.text.slice(0, 200) + (s.text.length > 200 ? "…" : "")
            : `${s.citation_id ? `[${s.citation_id}] ` : s.rank ? `#${s.rank} ` : ""}${s.filename || s.document_id.slice(0, 8) + "…"}${s.section_key ? ` · ${s.section_key}` : ""}${s.score ? ` (${s.score.toFixed(3)})` : ""}`}
        </button>
      ))}
    </div>
  );
}
