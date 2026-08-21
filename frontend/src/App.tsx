import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate, useLocation, Outlet } from "react-router-dom";
import { Layout } from '@/components/layout/Layout';

// Background
import AcidSquares from '@/components/background/AcidSquares';

// Contexts
import { ThemeProvider } from '@/contexts/ThemeContext';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';

// Components
import { TopNavBar } from '@/components/layout/TopNavBar';

// Pages
import LoginPage from '@/pages/auth/LoginPage';
import SignupPage from '@/pages/auth/SignupPage';
import { HomePage } from '@/pages/home/HomePage';
import WorkspacePage from '@/pages/workspace/WorkspacePage';
import WorkspaceDetailPage from '@/pages/workspace/WorkspaceDetailPage';
import ModelsPage from '@/pages/models/ModelsPage';
import DataConnectorsPage from '@/pages/connectors/DataConnectorsPage';
import NotebooksPage from '@/pages/notebooks/NotebooksPage';
import NotebookDetailPage from '@/pages/notebooks/NotebookDetailPage';
import FilesPage from '@/pages/files/FilesPage';
import SettingsPage from '@/pages/settings/SettingsPage';
import UserProfilePage from '@/pages/user/UserProfilePage';

const queryClient = new QueryClient();

function ProtectedRoute({ children }: { children: React.ReactNode }): JSX.Element {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <>{children}</>;
}

function AuthLayout(): JSX.Element {
  return (
    <div className="relative min-h-screen w-full overflow-hidden">
      {/* Background */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <AcidSquares
          color1="#3B82F6"
          color2="#F97316"
          color3="#FFFFFF"
          detail="medium"
          speed={0.7}
          waveDepth={1}
          zoom={1.3}
          density={10}
          glow={1}
          exposure={2700}
          spread={0.3}
          stepSize={0.002}
          colorShift={0}
          contrast={1}
          brightness={1}
          opacity={1}
          mouseInteraction
          mouseStrength={0.1}
          mouseRadius={0.35}
          blur={0}
          grain
          grainIntensity={0.05}
        />
      </div>

      {/* Navbar */}
      <div className="relative z-50">
        <TopNavBar
          isAuth={false}
        />
      </div>

      {/* Auth page */}
      <div className="relative z-10 flex min-h-[calc(100vh-4rem)] w-full items-center justify-center px-6 py-8">
        <Outlet />
      </div>
    </div>
  );
}

function ProtectedLayout({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <ProtectedRoute>
      <Layout>{children}</Layout>
    </ProtectedRoute>
  );
}

const App = () => (
  <ThemeProvider>
    <AuthProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <Toaster />
          <Sonner />
          <BrowserRouter>
            <Routes>
              <Route element={<AuthLayout />}>
                <Route path="/login" element={<LoginPage />} />
                <Route path="/signup" element={<SignupPage />} />
              </Route>
              <Route
                path="/"
                element={
                  <ProtectedLayout>
                    <HomePage />
                  </ProtectedLayout>
                }
              />
              <Route
                path="/workspace"
                element={
                  <ProtectedLayout>
                    <WorkspacePage />
                  </ProtectedLayout>
                }
              />
              <Route
                path="/workspace/:workspaceId"
                element={
                  <ProtectedLayout>
                    <WorkspaceDetailPage />
                  </ProtectedLayout>
                }
              />
              <Route
                path="/models"
                element={
                  <ProtectedLayout>
                    <ModelsPage />
                  </ProtectedLayout>
                }
              />
              <Route
                path="/data-connectors"
                element={
                  <ProtectedLayout>
                    <DataConnectorsPage />
                  </ProtectedLayout>
                }
              />
              <Route
                path="/notebooks"
                element={
                  <ProtectedLayout>
                    <NotebooksPage />
                  </ProtectedLayout>
                }
              />
              <Route
                path="/notebooks/:notebookId"
                element={
                  <ProtectedLayout>
                    <NotebookDetailPage />
                  </ProtectedLayout>
                }
              />
              <Route
                path="/files"
                element={
                  <ProtectedLayout>
                    <FilesPage />
                  </ProtectedLayout>
                }
              />
              <Route
                path="/profile"
                element={
                  <ProtectedLayout>
                    <UserProfilePage />
                  </ProtectedLayout>
                }
              />
              <Route
                path="/settings"
                element={
                  <ProtectedLayout>
                    <SettingsPage />
                  </ProtectedLayout>
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
        </TooltipProvider>
      </QueryClientProvider>
    </AuthProvider>
  </ThemeProvider>
);

export default App;