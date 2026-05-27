import { Navigate, Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { ChatPage } from "./pages/ChatPage";
import { PaperDetailPage } from "./pages/PaperDetailPage";
import { PapersPage } from "./pages/PapersPage";

export default function App() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main-surface">
        <Routes>
          <Route path="/" element={<Navigate to="/papers" replace />} />
          <Route path="/papers" element={<PapersPage />} />
          <Route path="/papers/:paperId" element={<PaperDetailPage />} />
          <Route path="/chat" element={<ChatPage />} />
        </Routes>
      </main>
    </div>
  );
}
