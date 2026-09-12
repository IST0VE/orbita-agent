import { useEffect, useRef, type ReactNode } from "react";

/** Native modal traps focus, makes the background inert, and restores focus. */
export function Modal({ children, label, className = "", onClose }: {
  children: ReactNode;
  label: string;
  className?: string;
  onClose?: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  // Capture before children with autoFocus mount.
  const opener = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  useEffect(() => {
    const dialog = ref.current;
    // `showModal()` на уже открытом окне бросает InvalidStateError, а StrictMode
    // в разработке прогоняет эффект дважды.
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      dialog?.close();
      if (opener.current?.isConnected) opener.current.focus();
    };
  }, []);
  return <dialog ref={ref} className={`overlay ${className}`} aria-label={label} aria-modal="true"
    onKeyDown={(event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onClose?.();
        return;
      }
      if (event.key !== "Tab") return;
      const controls = [...event.currentTarget.querySelectorAll<HTMLElement>(
        'button, input, select, textarea, a[href], summary, [tabindex], [contenteditable="true"]',
      )].filter((element) => element.tabIndex >= 0 && !element.matches(":disabled") && element.getClientRects().length > 0);
      const first = controls[0];
      const last = controls.at(-1);
      if (!first) { event.preventDefault(); event.currentTarget.focus(); }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    }}
    onCancel={(event) => { event.preventDefault(); onClose?.(); }}>
    {children}
  </dialog>;
}
