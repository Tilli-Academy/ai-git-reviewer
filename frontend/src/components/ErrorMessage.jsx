export default function ErrorMessage({ message }) {
  if (!message) return null;
  return (
    <div className="error-banner" role="alert">
      <strong>Error:</strong> {message}
    </div>
  );
}
