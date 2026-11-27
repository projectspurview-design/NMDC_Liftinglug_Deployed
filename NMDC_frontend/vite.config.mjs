import {defineConfig, loadEnv} from "vite";
import react from "@vitejs/plugin-react";

function backendProxy(target, remote) {
  return {
    target,
    changeOrigin: remote,
    cookieDomainRewrite: remote ? "" : undefined,
    configure(proxy) {
      if (!remote) return;
      proxy.on("proxyReq", (outgoing, incoming) => {
        const origin = incoming.headers.origin;
        if (origin && ["http", "https"].some(protocol => origin === `${protocol}://${incoming.headers.host}`)) {
          outgoing.setHeader("Origin", new URL(target).origin);
        }
      });
    },
  };
}

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, process.cwd(), "NMDC_");
  const remote = Boolean(env.NMDC_BACKEND_URL && env.NMDC_API_URL);
  const detector = backendProxy(env.NMDC_BACKEND_URL || "http://127.0.0.1:8001", remote);
  const fastApi = backendProxy(env.NMDC_API_URL || "http://127.0.0.1:8002", true);
  const proxy = {
    "^/auth(?:/|$)": detector,
    "^/system(?:/|$)": detector,
    "^/camera-admin(?:/|$)": detector,
    "^/camera(?:/|$)": detector,
    "/status": detector,
    "/health": detector,
    "/stream": detector,
    "/snapshot.jpg": detector,
    ...(remote && {
      // Account creation belongs to the documented FastAPI license service.
      "^/api/auth/licenses(?:/|$)": fastApi,
      // Sign-in uses the remote monitor's session for its protected camera feeds.
      "^/api/auth(?:/|$)": detector,
    }),
    "/api": fastApi,
  };
  return {
    plugins: [react()],
    define: {"import.meta.env.VITE_NMDC_REMOTE": JSON.stringify(remote)},
    server: {proxy},
    preview: {proxy},
  };
});
