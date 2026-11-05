/** The panel mark: a Clash-style cat head (the Kernel's own mascot) in the
 * clay palette. Same shape as `public/favicon.svg`, inlined so the sidebar and
 * the browser tab never drift apart. */
export function BrandMark({ className = "size-6.5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect width="32" height="32" rx="8" className="fill-accent-strong" />
      <path
        d="M9 6.2 13.4 10.4q2.6-1 5.2 0L23 6.2c.2 3.8.6 6.4.2 9.2C22.6 19.6 19.6 23.6 16 23.6s-6.6-4-7.2-8.2C8.4 12.6 8.8 10 9 6.2Z"
        className="fill-on-accent"
      />
      <circle cx="13" cy="15.6" r="1.45" className="fill-accent-strong" />
      <circle cx="19" cy="15.6" r="1.45" className="fill-accent-strong" />
    </svg>
  );
}
