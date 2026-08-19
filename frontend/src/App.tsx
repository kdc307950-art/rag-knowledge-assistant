import { useEffect } from "react";
import Login from "./pages/Login";
import ChatPanel from "./components/ChatPanel";
import Sidebar from "./components/Sidebar";
import StatusBar from "./components/StatusBar";
import { useAuth } from "./store/auth";

export default function App() {
  const isAuthenticated = useAuth((s) => s.isAuthenticated);
  const clearAuth = useAuth((s) => s.clearAuth);
  const restoreSession = useAuth((s) => s.restoreSession);
  const restoring = useAuth((s) => s.restoring);

  useEffect(() => {
    void restoreSession();
  }, [restoreSession]);

  useEffect(() => {
    // apiFetch emits this event after a 401. Do not call /auth/logout again:
    // the cookie may already be invalid and the extra request can recurse.
    const onLogout = () => clearAuth();
    window.addEventListener("auth:logout", onLogout);
    return () => window.removeEventListener("auth:logout", onLogout);
  }, [clearAuth]);

  if (restoring) {
    return <div className="flex min-h-dvh items-center justify-center bg-gray-50 text-sm text-gray-500">正在恢复登录状态...</div>;
  }
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
