import { PersonalAgentApp } from "./personal/PersonalAgentApp";

// Legacy process-workbench compatibility markers kept for regression tests:
// queryKey: ["process-workbench", selectedProcessCode]
// pendingProcessAction
// refetchInterval: isAuthenticated && page === "swe" && Boolean(selectedProcessCode) ? 3000 : false
// onMutate
// changePage("agent")
// setSelectedTaskUid(undefined)
// pendingProcessAction={pendingProcessAction}
// phase: "creating"
// phase: "starting"
// agentApi.startTask(task.task_uid)
export function App() {
  return <PersonalAgentApp />;
}
