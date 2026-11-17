import { useEffect, useState } from "react";
import { Shell, type PageKey } from "./components/Shell";
import { SetupWizard } from "./components/Wizard";
import { Toasts } from "./components/ui";
import { AlertsPage } from "./pages/Alerts";
import { BootScreen, LoginScreen, SetupScreen } from "./pages/Auth";
import { ClipboardPage } from "./pages/Clipboard";
import { DevicesPage } from "./pages/Devices";
import { NetworkPage } from "./pages/Network";
import { OverviewPage } from "./pages/Overview";
import { ResourcesPage } from "./pages/Resources";
import { SettingsPage } from "./pages/Settings";
import { StoragePage } from "./pages/Storage";
import { TasksPage } from "./pages/Tasks";
import { UpdatesPage } from "./pages/Updates";
import { useApp } from "./store";

export default function App() {
  const { status, wizardOpen, me } = useApp();
  const [page, setPage] = useState<PageKey>("overview");

  useEffect(() => {
    if (me?.node_name) document.title = `${me.node_name} · GOLEM LINK`;
  }, [me?.node_name]);

  if (status === "boot") {
    return (
      <>
        <BootScreen />
        <Toasts />
      </>
    );
  }

  if (status === "setup") {
    return (
      <>
        <SetupScreen />
        <Toasts />
      </>
    );
  }

  if (status === "login") {
    return (
      <>
        <LoginScreen />
        <Toasts />
      </>
    );
  }

  return (
    <>
      <Shell page={page} onNavigate={setPage}>
        {page === "overview" && <OverviewPage onNavigate={setPage} />}
        {page === "devices" && <DevicesPage onNavigate={setPage} />}
        {page === "resources" && <ResourcesPage />}
        {page === "tasks" && <TasksPage />}
        {page === "storage" && <StoragePage />}
        {page === "network" && <NetworkPage />}
        {page === "clipboard" && <ClipboardPage />}
        {page === "alerts" && <AlertsPage />}
        {page === "updates" && <UpdatesPage />}
        {page === "settings" && <SettingsPage />}
      </Shell>
      {wizardOpen && <SetupWizard />}
      <Toasts />
    </>
  );
}
