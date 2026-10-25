// Keyboard help of the editor (spec 5 WCAG 2.1 AA, jotdown H2-14): the "Ayuda de teclado" button,
// or the ? key anywhere but a text field, opens a modal dialog listing SHORTCUT_GROUPS. Focus
// moves into the dialog and Tab is trapped in it; Esc or the close button closes it and gives
// focus back to the element that had it. Keys pressed inside the dialog do not reach the
// editor's document-level shortcuts.
import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { isTypingTarget } from "../panels/Palette";
import { HELP_KEY, SHORTCUT_GROUPS, type ShortcutGroup } from "./shortcuts";

export const KEYBOARD_HELP_TEXT = {
  open: "Ayuda de teclado",
  title: "Atajos de teclado",
  close: "Cerrar",
  keys: "Teclas",
  action: "Acción",
} as const;

const styles = {
  backdrop: {
    position: "fixed",
    inset: 0,
    background: "rgba(20, 32, 43, 0.45)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  dialog: {
    background: "#fff",
    color: "#14202B",
    borderRadius: 8,
    padding: 24,
    maxWidth: 640,
    maxHeight: "85vh",
    overflowY: "auto",
  },
  table: { borderCollapse: "collapse", width: "100%", marginBottom: 16, fontSize: 14 },
  cell: { textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #D5DCE3" },
  kbd: { fontFamily: "ui-monospace,Menlo,monospace", whiteSpace: "nowrap" },
} satisfies Record<string, CSSProperties>;

const FOCUSABLE = "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])";

function ShortcutTable({ group }: { group: ShortcutGroup }) {
  return (
    <table style={styles.table}>
      <caption style={{ textAlign: "left", fontWeight: 600, padding: "8px 0 4px" }}>
        {group.title}
      </caption>
      <thead>
        <tr>
          <th scope="col" style={styles.cell}>
            {KEYBOARD_HELP_TEXT.keys}
          </th>
          <th scope="col" style={styles.cell}>
            {KEYBOARD_HELP_TEXT.action}
          </th>
        </tr>
      </thead>
      <tbody>
        {group.entries.map((entry) => (
          <tr key={`${entry.keys}-${entry.description}`}>
            <td style={{ ...styles.cell, ...styles.kbd }}>{entry.keys}</td>
            <td style={styles.cell}>{entry.description}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function KeyboardHelp() {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | SVGElement | null>(null);

  useEffect(() => {
    if (open) return;
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== HELP_KEY || event.ctrlKey || event.metaKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;
      event.preventDefault();
      const active = document.activeElement;
      returnFocus.current =
        active instanceof HTMLElement || active instanceof SVGElement ? active : null;
      setOpen(true);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useEffect(() => {
    if (open) dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
  }, [open]);

  function close() {
    setOpen(false);
    const target = returnFocus.current;
    returnFocus.current = null;
    target?.focus();
  }

  function onDialogKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // The editor's shortcuts listen on the document: keep keys typed here away from them.
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])];
    const first = focusable[0];
    const last = focusable.at(-1);
    if (first === undefined || last === undefined) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-keyshortcuts={HELP_KEY}
        title={`${KEYBOARD_HELP_TEXT.open} (${HELP_KEY})`}
        onClick={(event) => {
          returnFocus.current = event.currentTarget;
          setOpen(true);
        }}
      >
        {KEYBOARD_HELP_TEXT.open}
      </button>
      {open && (
        <div style={styles.backdrop}>
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            style={styles.dialog}
            onKeyDown={onDialogKeyDown}
          >
            <h2 id={titleId} style={{ marginTop: 0, fontSize: 20 }}>
              {KEYBOARD_HELP_TEXT.title}
            </h2>
            {SHORTCUT_GROUPS.map((group) => (
              <ShortcutTable key={group.title} group={group} />
            ))}
            <button type="button" onClick={close}>
              {KEYBOARD_HELP_TEXT.close}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
