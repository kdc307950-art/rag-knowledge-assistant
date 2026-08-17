import { useEffect } from "react";
import Login from "./pages/Login";
import ChatPanel from "./components/ChatPanel";
import Sidebar from "./components/Sidebar";
import StatusBar from "./components/StatusBar";
import { useAuth } from "./store/auth";

export default function App() {
  const isAuthenticated = useAuth((s) => s.isAuthenticated);
  const logout = useAuth((s) => s.logout);

  useEffect(() => {
    const onLogout = () => logout();
    window.addEventListener("auth:logout", onLogout);
    return () => window.removeEventListener("auth:logout", onLogout);
  }, [logout]);

  if (!isAuthenticated) {
    return <Login />;
  }

  return (
    <div className="flex min-h-dvh flex-col bg-gray-100">
      <StatusBar />
      <main className="min-h-0 flex-1 overflow-hidden">
        <div className="flex h-full min-h-0 flex-col md:flex-row">
          <Sidebar />
          <div className="min-h-0 flex-1">
            <ChatPanel />
          </div>
        </div>
      </main>
    </div>
  );
}
