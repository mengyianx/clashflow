import { useCallback, useState } from "react";

import { Alert, Verbatim } from "@/components/ui/alert";
import { ApiError } from "@/lib/api";
import { describeError, type MessageText } from "@/lib/messages";
import { useSession } from "@/lib/session";

/** Failures persist until dismissed (the Kernel's output is often several
 * lines); an expired session is handed to the shell instead of shown. */
export function useErrorBanner(): {
  banner: MessageText | null;
  show: (error: unknown) => void;
  showText: (title: string, detail?: string) => void;
  clear: () => void;
} {
  const { markSignedOut } = useSession();
  const [banner, setBanner] = useState<MessageText | null>(null);

  const show = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) {
        markSignedOut();
        return;
      }
      setBanner(describeError(error));
    },
    [markSignedOut],
  );
  const showText = useCallback((title: string, detail = "") => setBanner({ title, detail }), []);
  const clear = useCallback(() => setBanner(null), []);

  return { banner, show, showText, clear };
}

export function ErrorBanner({
  banner,
  onDismiss,
}: {
  banner: MessageText | null;
  onDismiss: () => void;
}) {
  if (!banner) return null;
  return (
    <Alert variant="bad" title={banner.title} onDismiss={onDismiss} className="mb-4">
      {banner.detail ? <Verbatim>{banner.detail}</Verbatim> : null}
    </Alert>
  );
}
