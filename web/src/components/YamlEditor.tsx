import { yaml } from "@codemirror/lang-yaml";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import CodeMirror from "@uiw/react-codemirror";
import { useMemo } from "react";

import { useTheme } from "@/hooks/useTheme";

/** YAML editor: CodeMirror 6 with the panel's own tokens (CodeMirror reads the
 * CSS variables, so light/dark needs no second theme object). */
const panelTheme = EditorView.theme({
  "&": { backgroundColor: "transparent", color: "var(--ink)", fontSize: "13px" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.7" },
  ".cm-content": { padding: "16px 0", caretColor: "var(--accent-strong)" },
  "&.cm-focused": { outline: "none" },
  ".cm-gutters": {
    backgroundColor: "transparent",
    color: "var(--ink-3)",
    borderRight: "1px solid var(--line-soft)",
    paddingRight: "6px",
  },
  ".cm-activeLine": { backgroundColor: "var(--hover)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--hover)" },
  ".cm-selectionBackground, ::selection": { backgroundColor: "var(--accent-soft)" },
  "&.cm-focused .cm-selectionBackground": { backgroundColor: "var(--accent-soft)" },
});

const panelHighlight = HighlightStyle.define([
  { tag: tags.propertyName, color: "var(--accent-strong)" },
  { tag: tags.string, color: "var(--ok)" },
  { tag: tags.number, color: "var(--warn)" },
  { tag: tags.bool, color: "var(--warn)" },
  { tag: tags.null, color: "var(--ink-3)" },
  { tag: tags.comment, color: "var(--ink-3)", fontStyle: "italic" },
  { tag: tags.punctuation, color: "var(--ink-2)" },
]);

export function YamlEditor({
  value,
  onChange,
}: {
  value: string;
  onChange?: (next: string) => void;
}) {
  const { theme } = useTheme();
  const extensions = useMemo(
    () => [yaml(), panelTheme, syntaxHighlighting(panelHighlight)],
    [],
  );

  return (
    <CodeMirror
      value={value}
      height="100%"
      theme={theme}
      extensions={extensions}
      basicSetup={{
        foldGutter: false,
        highlightActiveLine: true,
        highlightActiveLineGutter: true,
        autocompletion: false,
      }}
      onChange={(next) => onChange?.(next)}
      className="h-full"
    />
  );
}
