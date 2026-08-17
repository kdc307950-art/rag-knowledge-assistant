import { useEffect } from "react";
import Login from "./pages/Login";
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
    <div className="h-screen flex flex-col bg-gray-100">
      <StatusBar />
      <main className="flex-1 overflow-auto flex items-center justify-center text-gray-400">
        M1 完成：聊天面板将在 M2 接入
      </main>
    </div>
  );
}
