import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { useAuth } from "./hooks/useAuth";
import { ROUTES } from "./constants/routes";
import LoginPage from "./pages/LoginPage";
import SignupPage from "./pages/SignupPage";

/**
 * Inline placeholder for the "/" route.
 *
 * Deliberately not a separate page component (per Task 2.3 review
 * feedback: no temporary page that gets deleted later). This gets
 * replaced by the real dashboard when that task arrives; until then
 * it exists only to make the post-login/signup redirect demonstrable.
 * Not a protected route — it renders different content for logged-in
 * vs logged-out visitors, but doesn't block access either way.
 */
function AuthPlaceholder() {
  const { isAuthenticated, email, logout } = useAuth();

  if (isAuthenticated) {
    return (
      <main style={{ fontFamily: "sans-serif", padding: "2rem" }}>
        <h1>Logged in successfully</h1>
        <p>Authenticated as: {email}</p>
        <button onClick={logout}>Log out</button>
      </main>
    );
  }

  return (
    <main style={{ fontFamily: "sans-serif", padding: "2rem" }}>
      <h1>ResearchPilot AI</h1>
      <p>Not logged in.</p>
      <p>
        <Link to={ROUTES.login}>Log in</Link> or <Link to={ROUTES.signup}>sign up</Link>
      </p>
    </main>
  );
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path={ROUTES.home} element={<AuthPlaceholder />} />
          <Route path={ROUTES.login} element={<LoginPage />} />
          <Route path={ROUTES.signup} element={<SignupPage />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
