import { useNavigate, useLocation } from "react-router-dom";
import { ArrowLeft, Settings, Moon, Sun } from "lucide-react";
import { useTheme } from "@/contexts/ThemeContext";

interface TopNavBarProps {
  isAuth?: boolean;
}

export const TopNavBar = ({ isAuth = false }: TopNavBarProps) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { theme, toggleTheme } = useTheme();

  const showBackButton =
    isAuth &&
    location.pathname !== "/" &&
    location.pathname !== "/login" &&
    location.pathname !== "/signup";

  const handleBack = () => {
    navigate(-1);
  };

  const handleSettingsClick = () => {
    navigate("/settings");
  };

  return (
    <nav className="pointer-events-none absolute inset-x-0 top-0 z-50 flex h-16 items-center justify-between bg-transparent px-6">
      {/* Left Section */}
      <div className="pointer-events-auto">
        {showBackButton && (
          <button
            onClick={handleBack}
            className="flex h-10 w-10 items-center justify-center rounded-lg text-foreground/60 transition-colors hover:bg-foreground/5 hover:text-foreground"
            aria-label="Go back"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
        )}
      </div>

      {/* Right Section */}
      <div className="pointer-events-auto flex items-center gap-2">
        {/* Theme Toggle */}
        <button
          onClick={toggleTheme}
          className="flex h-10 w-10 items-center justify-center rounded-lg text-foreground/60 transition-colors hover:bg-foreground/5 hover:text-foreground"
          aria-label="Toggle theme"
        >
          {theme === "dark" ? (
            <Sun className="h-5 w-5" />
          ) : (
            <Moon className="h-5 w-5" />
          )}
        </button>

        {/* Settings - hidden during auth */}
        {isAuth && (
          <button
            onClick={handleSettingsClick}
            className="flex h-10 w-10 items-center justify-center rounded-lg text-foreground/60 transition-colors hover:bg-foreground/5 hover:text-foreground"
            aria-label="Settings"
          >
            <Settings className="h-5 w-5" />
          </button>
        )}
      </div>
    </nav>
  );
};