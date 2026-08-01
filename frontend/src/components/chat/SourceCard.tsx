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

function citedSourceIds(answer: string): Set<string> {
  const ids = new Set<string>();
  for (const group of answer.matchAll(/\[((?:(?:WS|S)\d+)(?:\s*[,，]\s*(?:WS|S)\d+)*)\]/gi)) {
    for (const id of group[1].match(/(?:WS|S)\d+/gi) || []) ids.add(id.toUpperCase());
  }
  return ids;
}

export function SourceCard({ sources, answer = "" }: { sources: Source[]; answer?: string }) {
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const citedIds = citedSourceIds(answer);
  const matchedCitedSources = citedIds.size
    ? sources.filter((source) => source.citation_id && citedIds.has(source.citation_id.toUpperCase()))
    : sources;
  // Invalid/stale citation IDs must not make the source strip appear empty.
  // Fall back to all retrieved evidence so the user can still inspect it.
  const citedSources = matchedCitedSources.length > 0 ? matchedCitedSources : sources;
  const visibleSources = showAll ? sources : citedSources;
  const hiddenCount = sources.length - citedSources.length;

  return (
    <div className="source-row">
      {visibleSources.map((s, i) => {
        const sourceKey = s.citation_id || `${s.document_id}-${s.section_key || ""}-${i}`;
        return (
        <button
          type="button"
          key={sourceKey}
          className={`source-chip ${activeKey === sourceKey ? "active" : ""}`}
          onClick={() => setActiveKey(activeKey === sourceKey ? null : sourceKey)}
          title={s.text.slice(0, 300)}
        >
          {activeKey === sourceKey
            ? s.text.slice(0, 200) + (s.text.length > 200 ? "…" : "")
            : `${s.citation_id ? `[${s.citation_id}] ` : s.rank ? `#${s.rank} ` : ""}${s.filename || s.document_id.slice(0, 8) + "…"}${s.section_key ? ` · ${s.section_key}` : ""}${s.score ? ` (${s.score.toFixed(3)})` : ""}`}
        </button>
        );
      })}
      {hiddenCount > 0 && (
        <button
          type="button"
          className="source-chip"
          onClick={() => {
            setShowAll(!showAll);
            setActiveKey(null);
          }}
          title={showAll ? "仅显示回答实际引用的来源" : "展开本轮其余检索结果"}
        >
          {showAll ? "仅显示已引用来源" : `查看其余 ${hiddenCount} 条检索结果`}
        </button>
      )}
    </div>
  );
}
