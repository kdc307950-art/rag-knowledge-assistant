import { useState } from "react";
import { useAuth } from "../store/auth";
import { getHealth } from "../api/health";
import { ApiError } from "../api/client";

export default function Login() {
  const login = useAuth((s) => s.login);
  const logout = useAuth((s) => s.logout);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      // 先写入 localStorage（client 才能带上 X-API-Key），再用 health 验证。
      login(password);
      await getHealth();
    } catch (err) {
      logout();
      if (err instanceof ApiError && err.status === 401) {
        setError("访问口令错误");
      } else if (err instanceof ApiError) {
        setError("后端错误：" + err.message);
      } else {
        setError("无法连接后端服务，请确认已启动 API（uvicorn backend.main:app --port 8000）");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <form onSubmit={submit} className="w-80 bg-white p-8 rounded-lg shadow">
        <h1 className="text-xl font-semibold text-center mb-1">企业知识库助手</h1>
        <p className="text-sm text-gray-500 text-center mb-6">配置了访问口令时请输入；本地模式可留空</p>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="访问口令（可选）"
          autoFocus
          className="w-full border rounded px-3 py-2 mb-4 outline-none focus:border-blue-500"
        />
        {error && <p className="text-sm text-red-600 mb-4">{error}</p>}
        <button
          type="submit"
          disabled={loading}
          className="w-full bg-blue-600 text-white rounded py-2 hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? "验证中..." : password.trim() ? "登录" : "进入本地模式"}
        </button>
      </form>
    </div>
  );
}
