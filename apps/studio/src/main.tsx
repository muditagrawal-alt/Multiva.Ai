import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import "./index.css";
import Home from "@/pages/Home";
import Studio from "@/pages/Studio";
import Setup from "@/pages/Setup";

const router = createBrowserRouter(
  [
    // Both screens own the whole window and supply their own title and
    // status bars. Home is the project manager the application opens on.
    { path: "/", element: <Home /> },
    { path: "/studio", element: <Studio /> },
    { path: "/setup", element: <Setup /> },
    // /library was the separate project browser. Home is that now; the route
    // stays so existing links and window state do not dead-end.
    { path: "/library", element: <Navigate to="/" replace /> },
  ],
  // FastAPI mounts the build at /app, so the router shares that base.
  { basename: "/app" }
);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>
);
