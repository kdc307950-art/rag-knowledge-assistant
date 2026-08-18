import { useState } from "react";
import { useAuth } from "../store/auth";
import { verifyLogin } from "./loginFlow";

export default function Login() {
  const prepareLogin = useAuth((s) => s.prepareLogin);
  const completeLogin = useAuth((s) => s.completeLogin);
  const loginUser = useAuth((s) => s.loginUser);
  const logout = useAuth((s) => s.logout);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      setError(await verifyLogin(password, { prepareLogin, completeLogin, logout, loginUser }, username));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <form onSubmit={submit} className="w-80 bg-white p-8 rounded-lg shadow">
        <h1 className="text-xl font-semibold text-center mb-1">企业知识库助手</h1>
        <p className="text-sm text-gray-500 text-center mb-6">多用户模式请输入用户名和密码；单用户模式仅填写访问口令</p>
        <input
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="用户名（多用户模式）"
          autoComplete="username"
          className="w-full border rounded px-3 py-2 mb-3 outline-none focus:border-blue-500"
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={username.trim() ? "密码" : "访问口令（可选）"}
          autoComplete={username.trim() ? "current-password" : "off"}
          autoFocus
          className="w-full border rounded px-3 py-2 mb-4 outline-none focus:border-blue-500"
        />
        {error && <p className="text-sm text-red-600 mb-4">{error}</p>}
        <button
          type="submit"
          disabled={loading}
          className="w-full bg-blue-600 text-white rounded py-2 hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? "验证中..." : username.trim() || password.trim() ? "登录" : "进入本地模式"}
        </button>
      </form>
    </div>
  );
}
  const [username, setUsername] = useState("");
