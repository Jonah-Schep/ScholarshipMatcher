import { isRouteErrorResponse, Link, useRouteError } from "react-router-dom";

/** Shown instead of a blank page when a page throws while rendering. */
export function ErrorPage() {
  const error = useRouteError();
  const detail = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : String(error);
  return (
    <main className="content">
      <h2>Something went wrong</h2>
      <div className="notice error">{detail}</div>
      <p>
        Your data is safe: it is stored by the ScholarMatch API, not in this page. <Link to="/">Start again</Link> or
        reload the page.
      </p>
    </main>
  );
}
