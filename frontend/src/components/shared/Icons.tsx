interface IconProps { size?: number; className?: string; style?: React.CSSProperties }

function i(props: IconProps, d: string, strokeWidth = 1.5) {
  const s = props.size ?? 16;
  return (
    <svg className={props.className} style={props.style} width={s} height={s} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth={strokeWidth}
         strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}

function i2(props: IconProps, d1: string, d2: string, sw = 1.5) {
  const s = props.size ?? 16;
  return (
    <svg className={props.className} width={s} height={s} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth={sw}
         strokeLinecap="round" strokeLinejoin="round">
      <path d={d1} />
      <path d={d2} />
    </svg>
  );
}

export function ChatIcon(p: IconProps)     { return i(p, "M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2zM8 9h8M8 13h6"); }
export function DocIcon(p: IconProps)      { return i2(p, "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z", "M14 2v6h6M16 13H8M16 17H8M10 9H8"); }
export function SettingsIcon(p: IconProps) { return i2(p, "M12 15a3 3 0 100-6 3 3 0 000 6z", "M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"); }
export function SendIcon(p: IconProps)     { return i(p, "M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"); }
export function StopIcon(p: IconProps)     { return i(p, "M6 6h4v12H6zM14 6h4v12h-4z", 2); }
export function PlusIcon(p: IconProps)     { return i(p, "M12 5v14M5 12h14"); }
export function TrashIcon(p: IconProps)    { return i2(p, "M3 6h18", "M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2M10 11v6M14 11v6"); }
export function CopyIcon(p: IconProps)     { return i2(p, "M8 4H6a2 2 0 00-2 2v12a2 2 0 002 2h6a2 2 0 002-2v-2", "M16 4h2a2 2 0 012 2v2M8 12h8"); }
export function CheckIcon(p: IconProps)    { return i(p, "M20 6L9 17l-5-5"); }
export function ChevronDownIcon(p: IconProps) { return i(p, "M6 9l6 6 6-6"); }
export function ChevronUpIcon(p: IconProps)   { return i(p, "M18 15l-6-6-6 6"); }
export function RefreshIcon(p: IconProps)  { return i2(p, "M21.5 2v6h-6", "M2.5 22v-6h6M2 11.5a10 10 0 0118.8-4.3M22 12.5a10 10 0 01-18.8 4.2"); }
export function SearchIcon(p: IconProps)   { return i2(p, "M11 19a8 8 0 100-16 8 8 0 000 16z", "M21 21l-4.35-4.35"); }
export function UploadIcon(p: IconProps)   { return i2(p, "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4", "M17 8l-5-5-5 5M12 3v12"); }
export function ArrowDownIcon(p: IconProps) { return i(p, "M12 5v14M19 12l-7 7-7-7"); }
export function BrainIcon(p: IconProps)    { return i2(p, "M12 2a4 4 0 014 4c0 1.5-.8 2.8-2 3.5V12a2 2 0 01-2 2h-1a2 2 0 01-2-2V9.5A4 4 0 0112 2z", "M9 18h6M12 14v4M8 22h8"); }
export function LogoutIcon(p: IconProps)   { return i2(p, "M10 17l5-5-5-5M15 12H3", "M15 3h4a2 2 0 012 2v14a2 2 0 01-2 2h-4"); }
export function ThumbUpIcon(p: IconProps)  { return i(p, "M7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3m7-2V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14z"); }
export function ThumbDownIcon(p: IconProps) { return i(p, "M17 2h3a2 2 0 012 2v7a2 2 0 01-2 2h-3m-7 2V5a3 3 0 013-3l4 9v11H5.72a2 2 0 01-2-1.7l-1.38-9a2 2 0 012-2.3H10z"); }
export function FilePdfIcon(p: IconProps)  { return i2(p, "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z", "M14 2v6h6M9 15v-4M12 15v-4M15 15v-4M9 11h6"); }
export function FileDocIcon(p: IconProps)  { return i2(p, "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z", "M14 2v6h6M9 13h3v4M9 13h6v4"); }
export function FileTxtIcon(p: IconProps)  { return i2(p, "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z", "M14 2v6h6M9 13h6M9 17h4"); }
export function FileCsvIcon(p: IconProps)  { return i2(p, "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z", "M14 2v6h6M8 15c.5 0 1 .5 1 1s-.5 1-1 1M12 15v4M16 15v4"); }
export function FileDefaultIcon(p: IconProps) { return i2(p, "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z", "M14 2v6h6"); }
export function CloseIcon(p: IconProps)    { return i(p, "M18 6L6 18M6 6l12 12"); }
export function WarnIcon(p: IconProps)     { return i2(p, "M12 9v4", "M12 17h.01M10.29 3.86l-8.6 14.86A1 1 0 002.56 20h18.88a1 1 0 00.87-1.28l-8.6-14.86a1 1 0 00-1.72 0z"); }
export function EditIcon(p: IconProps)    { return i2(p, "M12 20h9", "M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"); }
