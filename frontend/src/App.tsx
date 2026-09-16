import React, { useEffect, useState } from 'react';
import {
  Alert,
  AppBar,
  Box,
  Button,
  Chip,
  CircularProgress,
  Container,
  IconButton,
  Snackbar,
  Toolbar,
  Tooltip,
  Typography,
} from '@mui/material';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import SyncIcon from '@mui/icons-material/Sync';
import SettingsOutlinedIcon from '@mui/icons-material/SettingsOutlined';
import PriceCheckOutlinedIcon from '@mui/icons-material/PriceCheckOutlined';
import HistoryOutlinedIcon from '@mui/icons-material/HistoryOutlined';
import RefreshIcon from '@mui/icons-material/Refresh';

import { DisclaimerBanner } from './components/DisclaimerBanner';
import { KPISummary } from './components/KPISummary';
import { UserTable } from './components/UserTable';
import { UserDrawer } from './components/UserDrawer';
import { SettingsModal } from './components/SettingsModal';
import { PricingModal } from './components/PricingModal';
import { AuditModal } from './components/AuditModal';
import { AddUserModal } from './components/AddUserModal';

import {
  createUser,
  fetchConfig,
  fetchKPIs,
  fetchPricing,
  fetchUsers,
  publishAndSync,
  toggleUserLock,
  updateConfig,
  updatePricing,
  updateUser,
} from './services/api';
import { AppConfig, KPIStats, ModelPricing, User, UserUpdatePayload } from './types';

export const App: React.FC = () => {
  const [users, setUsers] = useState<User[]>([]);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [pricing, setPricing] = useState<Record<string, ModelPricing>>({});
  const [kpis, setKPIs] = useState<KPIStats | null>(null);

  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);

  // Modals & Drawer State
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [pricingOpen, setPricingOpen] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const [addUserOpen, setAddUserOpen] = useState(false);

  // Snackbar Notification
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'info' | 'warning' | 'error';
  }>({
    open: false,
    message: '',
    severity: 'info',
  });

  const loadData = async () => {
    try {
      const [usersData, configData, pricingData, kpisData] = await Promise.all([
        fetchUsers(),
        fetchConfig(),
        fetchPricing(),
        fetchKPIs(),
      ]);
      setUsers(usersData);
      setConfig(configData);
      setPricing(pricingData);
      setKPIs(kpisData);
    } catch (err) {
      console.error('Failed to load portal data:', err);
      showNotification('Failed to connect to backend service', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const showNotification = (
    message: string,
    severity: 'success' | 'info' | 'warning' | 'error' = 'success'
  ) => {
    setSnackbar({ open: true, message, severity });
  };

  const handleSelectUser = (user: User) => {
    setSelectedUser(user);
    setDrawerOpen(true);
  };

  const handleSaveUser = async (email: string, payload: UserUpdatePayload) => {
    try {
      const updated = await updateUser(email, payload);
      setUsers((prev) => prev.map((u) => (u.email === updated.email ? updated : u)));
      setSelectedUser(updated);
      const newKPIs = await fetchKPIs();
      setKPIs(newKPIs);
      showNotification(
        `Draft changes saved for ${email}. Click "Publish & Sync" to apply immediately to Cloud Identity.`,
        'info'
      );
    } catch (e: any) {
      showNotification(e.message || 'Error updating developer', 'error');
    }
  };

  const handleToggleLock = async (email: string) => {
    try {
      const updated = await toggleUserLock(email);
      setUsers((prev) => prev.map((u) => (u.email === updated.email ? updated : u)));
      const newKPIs = await fetchKPIs();
      setKPIs(newKPIs);
      showNotification(`Access status updated for ${email}`, 'success');
    } catch (e: any) {
      showNotification(e.message || 'Error changing access lock', 'error');
    }
  };

  const handleToggleExempt = async (user: User) => {
    await handleSaveUser(user.email, { is_exempt: !user.is_exempt });
  };

  const handleAddUser = async (payload: {
    email: string;
    is_exempt?: boolean;
    has_custom_quota?: boolean;
    custom_quota_usd?: number | null;
    custom_overage_usd?: number | null;
  }) => {
    const created = await createUser(payload);
    setUsers((prev) => [created, ...prev]);
    const newKPIs = await fetchKPIs();
    setKPIs(newKPIs);
    showNotification(`Developer ${created.email} pre-provisioned successfully!`, 'success');
  };

  const handleSaveConfig = async (payload: Partial<AppConfig>) => {
    const updated = await updateConfig(payload);
    setConfig(updated);
    showNotification('Portal settings updated successfully.', 'success');
  };

  const handleSavePricing = async (matrix: Record<string, ModelPricing>) => {
    const updated = await updatePricing(matrix);
    setPricing(updated);
    showNotification('Model pricing matrix updated successfully.', 'success');
  };

  const handlePublishAndSync = async () => {
    setSyncing(true);
    try {
      const result = await publishAndSync();
      if (result.success) {
        showNotification(
          `Publish & Sync complete: ${result.users_evaluated} developers evaluated, ${result.group_swaps_count} group membership updates executed.`,
          'success'
        );
        await loadData();
      } else {
        showNotification(result.error || 'Evaluation error during sync', 'error');
      }
    } catch (e: any) {
      showNotification(e.message || 'Failed to trigger publish & sync', 'error');
    } finally {
      setSyncing(false);
    }
  };

  return (
    <Box sx={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', bgcolor: '#f8f9fa' }}>
      {/* Top Application Bar */}
      <AppBar position="sticky" elevation={0} sx={{ bgcolor: '#ffffff', borderBottom: '1px solid #dadce0' }}>
        <Toolbar sx={{ justifyContent: 'space-between', px: { xs: 2, md: 4 } }}>
          {/* Brand Logo & Title */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                width: 38,
                height: 38,
                borderRadius: 2,
                bgcolor: '#1a73e8',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#ffffff',
              }}
            >
              <AutoAwesomeIcon fontSize="small" />
            </Box>
            <Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Typography variant="h6" sx={{ color: '#202124', fontWeight: 700, fontSize: 18 }}>
                  Antigravity Quota Portal
                </Typography>
                <Chip
                  label="Single-Container Cloud Run"
                  size="small"
                  sx={{ bgcolor: '#e8f0fe', color: '#1a73e8', fontWeight: 600, fontSize: 11 }}
                />
              </Box>
              <Typography variant="caption" sx={{ color: '#5f6368' }}>
                Gemini Enterprise AI Developer Access Governance
              </Typography>
            </Box>
          </Box>

          {/* Navigation Action Buttons */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Tooltip title="Refresh Dashboard">
              <IconButton size="small" onClick={loadData} disabled={loading}>
                <RefreshIcon />
              </IconButton>
            </Tooltip>

            <Button
              variant="text"
              size="small"
              startIcon={<HistoryOutlinedIcon />}
              onClick={() => setAuditOpen(true)}
              sx={{ color: '#5f6368' }}
            >
              Audit Trail
            </Button>

            <Button
              variant="text"
              size="small"
              startIcon={<PriceCheckOutlinedIcon />}
              onClick={() => setPricingOpen(true)}
              sx={{ color: '#5f6368' }}
            >
              Pricing Matrix
            </Button>

            <Button
              variant="text"
              size="small"
              startIcon={<SettingsOutlinedIcon />}
              onClick={() => setSettingsOpen(true)}
              sx={{ color: '#5f6368' }}
            >
              Settings
            </Button>

            {/* Instant Publish & Sync Button */}
            <Button
              variant="contained"
              size="medium"
              startIcon={syncing ? <CircularProgress size={16} color="inherit" /> : <SyncIcon />}
              onClick={handlePublishAndSync}
              disabled={syncing}
              sx={{
                bgcolor: '#188038',
                '&:hover': { bgcolor: '#137333' },
                fontWeight: 600,
                ml: 1,
              }}
            >
              {syncing ? 'Syncing...' : 'Publish & Sync'}
            </Button>
          </Box>
        </Toolbar>
      </AppBar>

      {/* Main Container Body */}
      <Container maxWidth="xl" sx={{ py: 3, flexGrow: 1 }}>
        {/* Global Estimation Notice Banner */}
        <DisclaimerBanner />

        {/* Top KPI Metric Cards */}
        <KPISummary kpis={kpis} loading={loading} />

        {/* User Management Data Table */}
        <UserTable
          users={users}
          onSelectUser={handleSelectUser}
          onToggleLock={handleToggleLock}
          onToggleExempt={handleToggleExempt}
          onOpenAddUser={() => setAddUserOpen(true)}
        />
      </Container>

      {/* Slide-out Drawer */}
      <UserDrawer
        user={selectedUser}
        config={config}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onSave={handleSaveUser}
      />

      {/* Modals */}
      <SettingsModal
        config={config}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSave={handleSaveConfig}
      />

      <PricingModal
        pricing={pricing}
        open={pricingOpen}
        onClose={() => setPricingOpen(false)}
        onSave={handleSavePricing}
      />

      <AuditModal open={auditOpen} onClose={() => setAuditOpen(false)} />

      <AddUserModal
        config={config}
        open={addUserOpen}
        onClose={() => setAddUserOpen(false)}
        onAdd={handleAddUser}
      />

      {/* Snackbar Alerts */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert
          onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
          severity={snackbar.severity}
          variant="filled"
          sx={{ width: '100%', fontWeight: 500 }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};
export default App;
