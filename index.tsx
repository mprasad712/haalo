import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { useGetConfig } from "@/controllers/API/queries/config/use-get-config";
import { useGetBasicExamplesQuery } from "@/controllers/API/queries/agents/use-get-basic-examples";
import { useGetFoldersQuery } from "@/controllers/API/queries/folders/use-get-folders";
import { useGetTagsQuery } from "@/controllers/API/queries/store";
import { useGetGlobalVariables } from "@/controllers/API/queries/variables";
import { useGetVersionQuery } from "@/controllers/API/queries/version";
import { useGetCurrentReleaseVersionQuery } from "@/controllers/API/queries/releases";
import { ENABLE_AGENTCORE_STORE } from "@/customization/feature-flags";
import { CustomLoadingPage } from "@/customization/components/custom-loading-page";
import { useCustomPrimaryLoading } from "@/customization/hooks/use-custom-primary-loading";
import { useDarkStore } from "@/stores/darkStore";
import useAgentsManagerStore from "@/stores/agentsManagerStore";
import { LoadingPage } from "../LoadingPage";

export function AppInitPage() {
  const refreshStars = useDarkStore((state) => state.refreshStars);
  const isLoading = useAgentsManagerStore((state) => state.isLoading);

  const { isFetched: isLoaded } = useCustomPrimaryLoading();

  useGetVersionQuery({ enabled: isLoaded });
  useGetCurrentReleaseVersionQuery({ enabled: isLoaded });
  const { isFetched: isConfigFetched } = useGetConfig({ enabled: isLoaded });
  useGetGlobalVariables({ enabled: isLoaded });
  useGetTagsQuery({ enabled: isLoaded && ENABLE_AGENTCORE_STORE });
  useGetFoldersQuery({ enabled: isLoaded });

  const { isFetched: isExamplesFetched, refetch: refetchExamples } =
    useGetBasicExamplesQuery({ enabled: isLoaded });

  useEffect(() => {
    if (isLoaded) {
      refreshStars();
    }

    if (isConfigFetched) {
      refetchExamples();
    }
  }, [isLoaded, isConfigFetched]);

  return (
    <>
      {isLoaded ? (
        (isLoading || !isExamplesFetched) && <LoadingPage overlay />
      ) : (
        <CustomLoadingPage />
      )}

      {isLoaded && isExamplesFetched && <Outlet />}
    </>
  );
}
