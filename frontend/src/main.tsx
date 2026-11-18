import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { Layout } from "./Layout";
import { AdvisorPage } from "./pages/AdvisorPage";
import { DataPage } from "./pages/DataPage";
import { ErrorPage } from "./pages/ErrorPage";
import { MatchesPage } from "./pages/MatchesPage";
import { ProfilePage } from "./pages/ProfilePage";
import { SavedPage } from "./pages/SavedPage";
import { StartPage } from "./pages/StartPage";
import "@fontsource-variable/fraunces";
import "@fontsource-variable/inter";
import "./styles.css";

// Every page but the start page belongs to one profile: /p/<profile id>/<page>.
const router = createBrowserRouter([
  {
    element: <Layout />,
    errorElement: <ErrorPage />,
    children: [
      { path: "/", element: <StartPage /> },
      { path: "/p/:pid", element: <Navigate to="matches" replace /> },
      { path: "/p/:pid/profile", element: <ProfilePage /> },
      { path: "/p/:pid/matches", element: <MatchesPage /> },
      { path: "/p/:pid/saved", element: <SavedPage /> },
      { path: "/p/:pid/advisor/:key?", element: <AdvisorPage /> },
      { path: "/p/:pid/data", element: <DataPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
