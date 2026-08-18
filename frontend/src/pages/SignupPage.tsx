import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ROUTES } from "../constants/routes";
import Button from "../components/Button";
import FormField from "../components/FormField";

export default function SignupPage() {
  const { signup } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await signup({ email, username, password });
      navigate(ROUTES.home);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Signup failed");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1>Sign up</h1>
        <p className="auth-card__subtitle">Create your ResearchPilot AI account.</p>
        <form onSubmit={handleSubmit}>
          <FormField
            id="email"
            label="Email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <FormField
            id="username"
            label="Username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            minLength={3}
            maxLength={50}
            required
          />
          <FormField
            id="password"
            label="Password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
          {error && (
            <p className="auth-card__form-error" role="alert">
              {error}
            </p>
          )}
          <Button type="submit" isLoading={isSubmitting} fullWidth>
            {isSubmitting ? "Signing up…" : "Sign up"}
          </Button>
        </form>
        <p className="auth-card__footer">
          Already have an account? <Link to={ROUTES.login}>Log in</Link>
        </p>
      </div>
    </main>
  );
}
