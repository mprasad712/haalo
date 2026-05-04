import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useResetPasswordWithToken } from "@/controllers/API/queries/auth";
import MothersonLogo from "@/assets/micore.svg";

export default function ResetPasswordPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  const { mutate: resetPassword } = useResetPasswordWithToken();

  useEffect(() => {
    if (!token) navigate("/forgot-password");
  }, [token, navigate]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (newPassword !== confirmPassword) {
      setError(t("Passwords do not match"));
      return;
    }
    if (newPassword.length < 8) {
      setError(t("Password must be at least 8 characters"));
      return;
    }
    resetPassword(
      { token, new_password: newPassword },
      {
        onSuccess: () => setSuccess(true),
        onError: (err: any) =>
          setError(
            err?.response?.data?.detail || t("Invalid or expired reset link"),
          ),
      },
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-50 via-white to-gray-100 px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 flex justify-center">
          <img src={MothersonLogo} alt="MiCore" className="h-16 w-auto" />
        </div>

        <div className="bg-white rounded-2xl shadow-xl border border-gray-200 p-8">
          {!success ? (
            <>
              <h2 className="text-2xl font-semibold text-gray-900 mb-2">
                {t("Set New Password")}
              </h2>
              <p className="text-sm text-gray-600 mb-6">
                {t("Choose a new password for your account.")}
              </p>
              <form onSubmit={handleSubmit} className="space-y-4">
                <input
                  type="password"
                  required
                  autoComplete="new-password"
                  placeholder={t("New password")}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full h-11 px-4 rounded-lg border border-gray-200 text-sm focus:outline-none focus:ring-2 focus:ring-[#da2128]/30"
                />
                <input
                  type="password"
                  required
                  autoComplete="new-password"
                  placeholder={t("Confirm new password")}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full h-11 px-4 rounded-lg border border-gray-200 text-sm focus:outline-none focus:ring-2 focus:ring-[#da2128]/30"
                />
                {error && <p className="text-sm text-red-500">{error}</p>}
                <Button
                  type="submit"
                  className="w-full h-11 !bg-[#da2128] hover:!bg-[#b81c22] text-white rounded-lg font-medium text-sm"
                >
                  {t("Set Password")}
                </Button>
              </form>
            </>
          ) : (
            <div className="text-center">
              <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <svg
                  className="w-8 h-8 text-green-500"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <h2 className="text-xl font-semibold text-gray-900 mb-2">
                {t("Password Updated")}
              </h2>
              <p className="text-sm text-gray-600 mb-6">
                {t("Your password has been set. You can now sign in.")}
              </p>
              <Button
                onClick={() => navigate("/login")}
                className="w-full h-11 !bg-[#da2128] hover:!bg-[#b81c22] text-white rounded-lg font-medium text-sm"
              >
                {t("Sign In")}
              </Button>
            </div>
          )}

          <div className="mt-6 text-center">
            <Link to="/login" className="text-sm text-gray-500 hover:text-gray-700">
              {t("← Back to Sign In")}
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
