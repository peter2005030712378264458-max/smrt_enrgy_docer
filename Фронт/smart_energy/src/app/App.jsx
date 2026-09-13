import AuthPage from '../pages/AuthPage.jsx'
import DashboardPage from '../pages/DashboardPage.jsx'
import AnalyticsPage from '../pages/dashboard/AnalyticsPage.jsx'
import ConnectionsPage from '../pages/dashboard/ConnectionsPage.jsx'
import DashboardOverviewPage from '../pages/dashboard/DashboardOverviewPage.jsx'
import { Route, Routes } from 'react-router'
import { ROUTES } from './routes.js'

function App() {
  return (
    <Routes>
      <Route element={<AuthPage />}>
        <Route element={<DashboardPage />}>
          <Route path={ROUTES.dashboard} element={<DashboardOverviewPage />} />
          <Route path={ROUTES.connections} element={<ConnectionsPage />} />
          <Route path={ROUTES.analytics} element={<AnalyticsPage />} />
        </Route>
      </Route>
      <Route path="*" element={<AuthPage />} />
    </Routes>
  )
}

export default App
