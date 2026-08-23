import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { Layout } from "./components/Layout";
import { EvidencePage } from "./routes/EvidencePage";
import { HomePage } from "./routes/HomePage";
import { NotFoundPage } from "./routes/NotFoundPage";
import { PaperPage } from "./routes/PaperPage";
import { ReportPage } from "./routes/ReportPage";
import { RunPage } from "./routes/RunPage";
import { CandidatePage } from "./routes/CandidatePage";
import { I18nProvider } from "./i18n";

export const router = createBrowserRouter([{ element: <Layout/>, children: [{ path: "/", element: <HomePage/> }, { path: "/runs/:id", element: <RunPage/> }, { path: "/runs/:id/report", element: <ReportPage/> }, { path: "/runs/:id/candidates/:candidateId", element: <CandidatePage/> }, { path: "/runs/:id/papers/:paperId", element: <PaperPage/> }, { path: "/runs/:id/evidence/:evidenceId", element: <EvidencePage/> }, { path: "*", element: <NotFoundPage/> }] }]);
export function App() { return <I18nProvider><RouterProvider router={router}/></I18nProvider>; }
