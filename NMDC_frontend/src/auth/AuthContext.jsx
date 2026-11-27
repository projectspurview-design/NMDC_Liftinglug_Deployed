import {createContext, useContext, useEffect, useState} from "react";
import {apiClient, apiErrorMessage, csrfHeaders, remoteBackend, setCsrfToken} from "../api/axios";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

export async function authRequest(path, body) {
  const endpoint = `/api/auth/${({session: "me", accounts: "licenses"})[path] ?? path}`;
  try {
    const {data} = await apiClient.request({
      url: endpoint,
      method: body === undefined ? "GET" : "POST",
      data: body,
      skipSessionExpired: path === "session" || path === "login",
      headers: ["accounts", "logout"].includes(path)
        ? csrfHeaders()
        : undefined,
    });
    if (["session", "login"].includes(path)) setCsrfToken(data?.csrf_token);
    if (path === "logout") setCsrfToken(undefined);
    return data;
  } catch (error) {
    if (path === "session" && error.response?.status === 401) {
      setCsrfToken(undefined);
      return {user: null, setup_required: false};
    }
    throw new Error(apiErrorMessage(error, error.response ? "Request failed. Please try again." : "Cannot reach the login service."));
  }
}

export function AuthProvider({children}) {
  const [user, setUser] = useState(null);
  const [aiToken, setAiToken] = useState(null);
  const [loading, setLoading] = useState(true);
  const [setupRequired, setSetupRequired] = useState(false);
  const [error, setError] = useState("");
  async function refresh() {
    try {
      const result = await authRequest("session");
      setUser(result.user);
      if (!result.user) setAiToken(null);
      setSetupRequired(!remoteBackend && Boolean(result.setup_required));
      setError("");
    } catch {
      setUser(null);
      setAiToken(null);
      setError("Cannot reach the login service. Check that the detector is running.");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 60000);
    const expired = () => { setUser(null); setAiToken(null); };
    window.addEventListener("session-expired", expired);
    return () => { clearInterval(timer); window.removeEventListener("session-expired", expired); };
  }, []);
  async function login(licenseId, password) {
    const result = await authRequest(setupRequired ? "setup" : "login", {license_id: licenseId, password});
    setAiToken(null);
    setUser(result.user);
    setSetupRequired(false);
    setError("");
  }
  async function logout() {
    await authRequest("logout", {});
    setUser(null);
    setAiToken(null);
  }
  return <AuthContext.Provider value={{user, aiToken, setAiToken, loading, setupRequired, error, login, logout, refresh}}>{children}</AuthContext.Provider>;
}
