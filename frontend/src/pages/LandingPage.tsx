import { useNavigate } from "react-router-dom";
import Button from "../components/Button";
import { ROUTES } from "../constants/routes";

/**
 * The application's entry experience for logged-out visitors —
 * replaces App.tsx's previous AuthPlaceholder (an explicitly
 * temporary Task 2.3 stand-in). Deliberately concise: no pricing,
 * testimonials, or marketing sections — this is an app entry point,
 * not a marketing site.
 */
export default function LandingPage() {
  const navigate = useNavigate();

  return (
    <main className="landing">
      <div className="landing__content">
        <p className="landing__eyebrow">ResearchPilot AI</p>
        <h1 className="landing__title">AI-powered research workspace</h1>
        <p className="landing__description">
          An AI-powered research workspace for interacting with papers and documents —
          upload, process, and ask grounded questions answered directly from their content.
        </p>
        <div className="landing__actions">
          <Button variant="primary" onClick={() => navigate(ROUTES.signup)}>
            Start Researching
          </Button>
          <Button variant="secondary" onClick={() => navigate(ROUTES.login)}>
            Log in
          </Button>
        </div>
      </div>
    </main>
  );
}
