import DashboardPage from "./pages/DashboardPage.jsx";

export default function App() {
  return (
    <>
      <header className="app-header">
        <div className="app-header__inner">
          <h1>AI GitHub PR Reviewer</h1>
          <p>
            Paste a public GitHub pull request URL to get an AI-assisted,
            first-level code review.
          </p>
        </div>
      </header>
      <DashboardPage />
    </>
  );
}
