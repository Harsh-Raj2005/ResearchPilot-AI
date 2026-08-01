import { useEffect, useState } from "react";
import { get } from "./services/api";

/**
 * Task 1 scope: this is a skeleton verification screen, not a real page.
 * It confirms the frontend can reach the backend's health endpoint.
 * DashboardPage / LoginPage / etc. replace this in the auth and
 * upload tasks — this component goes away then.
 */

interface HealthResponse {
  status: string;
  app: string;
  environment: string;
  database: string;
}

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<HealthResponse>("/api/v1/health")
      .then(setHealth)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <main style={{ fontFamily: "sans-serif", padding: "2rem" }}>
      <h1>ResearchPilot AI</h1>
      <p>Project skeleton — Task 1 verification screen.</p>
      {health && (
        <pre>{JSON.stringify(health, null, 2)}</pre>
      )}
      {error && (
        <p style={{ color: "crimson" }}>
          Could not reach backend: {error} (expected if the backend/DB
          isn&apos;t running locally)
        </p>
      )}
    </main>
  );
}

export default App;
