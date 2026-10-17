import { useEffect, useState } from "react";
import { api } from "./api";
import { StudioApp } from "./studio/StudioApp";
import { LegacyApp } from "./LegacyApp";

/**
 * Default entry: H3 studio layout.
 * Open with ?legacy=1 to use the previous wizard pipeline.
 */
export default function App() {
  const [serverHealth, setServerHealth] = useState<any>(null);
  const legacy =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("legacy") === "1";

  useEffect(() => {
    api
      .healthCheck()
      .then(setServerHealth)
      .catch(() => setServerHealth({ status: "offline" }));
  }, []);

  if (legacy) {
    return <LegacyApp initialHealth={serverHealth} />;
  }

  return <StudioApp serverHealth={serverHealth} />;
}
