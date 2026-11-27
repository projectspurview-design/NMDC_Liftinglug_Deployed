import Header from "../components/Header";
import CameraMaintenance from "./CameraMaintenance";

export default function CameraSettings() {
  return (
    <>
      <Header />
      <main className="camera-settings-page">
        <CameraMaintenance />
      </main>
    </>
  );
}
