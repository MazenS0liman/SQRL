// @/components/layout/Layout.tsx
import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Sidebar } from '@/components/common/Sidebar';
import { TopNavBar } from './TopNavBar';

interface LayoutProps {
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const hideTopNav1 = /^\/notebooks\/[^/]+$/.test(location.pathname);
  const hideTopNav2 = /^\/workspace\/[^/]+$/.test(location.pathname);

  // Determine active tab based on current route
  const getActiveTab = (): 'home' | 'workspace' | 'notebooks' | 'files' | 'models' | 'data connectors' => {
    const path = location.pathname;

    if (path.startsWith('/workspace')) return 'workspace';
    if (path.startsWith('/notebooks')) return 'notebooks';
    if (path.startsWith('/files')) return 'files';
    if (path.startsWith('/models')) return 'models';
    if (path.startsWith('/data-connectors')) return 'data connectors';

    return 'home';
  };

  const handleTabChange = (tab: 'home' | 'workspace' | 'notebooks' | 'files' | 'models' | 'data connectors') => {
    if (tab === 'home') {
      navigate('/');
    } else if (tab === 'workspace') {
      navigate('/workspace');
    } else if (tab === 'notebooks') {
      navigate('/notebooks');
    } else if (tab === 'files') {
      navigate('/files');
    } else if (tab === 'models') {
      navigate('/models');
    } else if (tab === 'data connectors') {
      navigate('/data-connectors');
    }
  };

  // Theme is applied globally to <html> by ThemeProvider, so it reflects on
  // every page without needing a class here too.
  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar
        activeTab={getActiveTab()}
        onTabChange={handleTabChange}
      />
      <div className="relative flex-1 flex flex-col min-w-0">
        {!hideTopNav1 && !hideTopNav2 && <TopNavBar isAuth={true} />}
        <div className="h-full overflow-y-auto">
          {children}
        </div>
      </div>
    </div>
  );
};