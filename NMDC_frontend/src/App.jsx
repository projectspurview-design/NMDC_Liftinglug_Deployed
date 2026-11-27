import {HashRouter, Navigate, Route, Routes} from "react-router-dom";
import {AuthProvider, useAuth} from "./auth/AuthContext";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import BayMonitor from "./pages/BayMonitor";
import CameraSettings from "./pages/CameraSettings";
import PerformanceDashboard from "./pages/PerformanceDashboard";
import EngagementDashboard, {EngagementLayout} from "./pages/EngagementDashboard";
import EngagementEventLogs from "./pages/EngagementEventLogs";

function RequireLogin({children, admin = false}) {
  const {user, loading} = useAuth();
  if (loading) return <div className="auth-loading" role="status">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (admin && user.role !== "admin") return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
    <HashRouter>
      <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<RequireLogin><Dashboard /></RequireLogin>} />
        <Route path="/bay/:id" element={<RequireLogin><BayMonitor /></RequireLogin>} />
        <Route path="/settings" element={<RequireLogin admin><CameraSettings /></RequireLogin>} />
        <Route path="/performance" element={<RequireLogin admin><PerformanceDashboard /></RequireLogin>} />
        <Route path="/engagements" element={<RequireLogin admin><EngagementLayout /></RequireLogin>}>
          <Route index element={<EngagementDashboard />} />
          <Route path="events" element={<EngagementEventLogs />} />
        </Route>
        <Route path="/settings/maintenance" element={<Navigate to="/settings" replace />} />
        <Route path="/maintenance" element={<Navigate to="/settings" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </AuthProvider>
    </HashRouter>
  );
}
