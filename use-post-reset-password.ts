import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export const useResetPasswordWithToken = (options?: any) => {
  const { mutate } = UseRequestProcessor();

  const mutationFn = async ({
    token,
    new_password,
  }: {
    token: string;
    new_password: string;
  }): Promise<{ message: string }> => {
    const res = await api.post(`${getURL("RESET_PASSWORD")}`, { token, new_password });
    return res.data;
  };

  return mutate(["useResetPasswordWithToken"], mutationFn, options);
};
