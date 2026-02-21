import { useWebSocket } from "./hooks/useWebSocket";
import { AppShell } from "./components/layout/AppShell";

export default function App() {
  useWebSocket();
  return <AppShell />;
}
