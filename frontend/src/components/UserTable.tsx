import React, { useState } from 'react';
import {
  Avatar,
  Box,
  Button,
  Chip,
  IconButton,
  InputAdornment,
  LinearProgress,
  Menu,
  MenuItem,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  TextField,
  Typography,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import LockOpenIcon from '@mui/icons-material/LockOpen';
import LockIcon from '@mui/icons-material/Lock';
import VerifiedUserOutlinedIcon from '@mui/icons-material/VerifiedUserOutlined';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import PersonAddOutlinedIcon from '@mui/icons-material/PersonAddOutlined';
import { ProjectWTUPoolStatus, User } from '../types';

interface UserTableProps {
  users: User[];
  projectPools?: ProjectWTUPoolStatus[];
  selectedProject?: string;
  defaultProject?: string;
  defaultResetUtc?: string | null;
  pendingUserEmail?: string | null;
  onSelectUser: (user: User) => void;
  onToggleLock: (email: string) => void;
  onToggleExempt: (user: User) => void;
  onOpenAddUser: () => void;
}

export const UserTable: React.FC<UserTableProps> = ({
  users,
  projectPools,
  selectedProject = 'ALL',
  defaultProject = 'your-workload-project',
  defaultResetUtc,
  pendingUserEmail = null,
  onSelectUser,
  onToggleLock,
  onToggleExempt,
  onOpenAddUser,
}) => {
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('ALL');
  const [sortField, setSortField] = useState<'gross_spend' | 'tokens' | 'email' | 'utilization' | 'remaining_credit'>('gross_spend');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // Menu action state
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);
  const [activeMenuUser, setActiveMenuUser] = useState<User | null>(null);

  const handleOpenMenu = (event: React.MouseEvent<HTMLElement>, user: User) => {
    event.stopPropagation();
    setMenuAnchor(event.currentTarget);
    setActiveMenuUser(user);
  };

  const handleCloseMenu = () => {
    setMenuAnchor(null);
    setActiveMenuUser(null);
  };

  const handleSort = (field: 'gross_spend' | 'tokens' | 'email' | 'utilization' | 'remaining_credit') => {
    if (sortField === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortOrder('desc');
    }
  };

  const formatRelativeTime = (isoString?: string | null) => {
    if (!isoString) return 'Inactive';
    const now = new Date();
    const date = new Date(isoString);
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    return `${diffDays}d ago`;
  };

  const formatWeeklyReset = (isoString?: string | null, fallbackResetUtc?: string | null) => {
    const target = isoString || fallbackResetUtc || defaultResetUtc;
    if (!target) return { label: '7-Day WTU Cycle', countdown: '' };
    try {
      const resetDate = new Date(target);
      const now = new Date();
      const diffSec = Math.max(0, (resetDate.getTime() - now.getTime()) / 1000);
      const days = Math.floor(diffSec / 86400);
      const hours = Math.floor((diffSec % 86400) / 3600);
      const formatted = resetDate.toLocaleString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
      return {
        label: formatted,
        countdown: `in ${days}d ${hours}h`,
      };
    } catch {
      return { label: target, countdown: '' };
    }
  };

  const getStatusChip = (user: User) => {
    if (user.status === 'MANUALLY_DISABLED') {
      return (
        <Chip
          label="Locked"
          size="small"
          icon={<LockIcon sx={{ fontSize: '14px !important', color: '#5f6368 !important' }} />}
          sx={{ backgroundColor: '#f1f3f4', color: '#5f6368', fontWeight: 600 }}
        />
      );
    }
    if (user.is_exempt) {
      return (
        <Chip
          label="Exempt"
          size="small"
          sx={{ backgroundColor: '#f3e8fd', color: '#7b1fa2', fontWeight: 600 }}
        />
      );
    }
    if (user.status === 'AUTO_DISABLED') {
      return (
        <Chip
          label="Auto-Throttled"
          size="small"
          sx={{ backgroundColor: '#fce8e6', color: '#d93025', fontWeight: 600 }}
        />
      );
    }
    if (user.current_week.credit_utilization_percentage >= 80) {
      return (
        <Chip
          label="Warning (>80%)"
          size="small"
          sx={{ backgroundColor: '#fef7e0', color: '#b06000', fontWeight: 600 }}
        />
      );
    }
    return (
      <Chip
        label="Active"
        size="small"
        sx={{ backgroundColor: '#e6f4ea', color: '#188038', fontWeight: 600 }}
      />
    );
  };

  const getUtilizationColor = (utilization: number) => {
    if (utilization > 100) return '#d93025'; // Red
    if (utilization >= 80) return '#f9ab00'; // Amber
    return '#188038'; // Green
  };

  // Filter users
  const filteredUsers = users.filter((u) => {
    const matchesSearch = u.email.toLowerCase().includes(search.toLowerCase());
    if (!matchesSearch) return false;

    const userProject = u.project_id || defaultProject;
    if (selectedProject !== 'ALL' && userProject !== selectedProject) {
      return false;
    }

    if (filterStatus === 'ALL') return true;
    if (filterStatus === 'LOCKED') return u.status === 'MANUALLY_DISABLED';
    if (filterStatus === 'EXEMPT') return u.is_exempt && u.status !== 'MANUALLY_DISABLED';
    if (filterStatus === 'THROTTLED') return u.status === 'AUTO_DISABLED';
    if (filterStatus === 'WARNING') return u.status === 'ACTIVE' && !u.is_exempt && u.current_week.credit_utilization_percentage >= 80;
    if (filterStatus === 'ACTIVE') return u.status === 'ACTIVE' && !u.is_exempt;
    return true;
  });

  // Sort users
  filteredUsers.sort((a, b) => {
    let comp = 0;
    if (sortField === 'gross_spend') {
      comp = a.current_week.gross_spend_usd - b.current_week.gross_spend_usd;
    } else if (sortField === 'tokens') {
      comp = a.current_week.total_tokens - b.current_week.total_tokens;
    } else if (sortField === 'email') {
      comp = a.email.localeCompare(b.email);
    } else if (sortField === 'utilization') {
      comp = a.current_week.credit_utilization_percentage - b.current_week.credit_utilization_percentage;
    } else if (sortField === 'remaining_credit') {
      comp = a.current_week.remaining_credit_usd - b.current_week.remaining_credit_usd;
    }
    return sortOrder === 'desc' ? -comp : comp;
  });

  return (
    <Paper elevation={0} sx={{ border: '1px solid #dadce0', borderRadius: 2, overflow: 'hidden' }}>
      {/* Search & Filter Header Bar */}
      <Box
        sx={{
          p: 2,
          display: 'flex',
          flexWrap: 'wrap',
          gap: 2,
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid #dadce0',
          backgroundColor: '#fafbfc',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: '1 1 300px', maxWidth: 450 }}>
          <TextField
            size="small"
            fullWidth
            placeholder="Search developer by email..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon sx={{ color: '#5f6368', fontSize: 20 }} />
                </InputAdornment>
              ),
            }}
          />
        </Box>

        {/* Filter Pills */}
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
          {['ALL', 'ACTIVE', 'WARNING', 'THROTTLED', 'LOCKED', 'EXEMPT'].map((st) => (
            <Chip
              key={st}
              label={st === 'ALL' ? 'All Developers' : st.charAt(0) + st.slice(1).toLowerCase()}
              clickable
              color={filterStatus === st ? 'primary' : 'default'}
              variant={filterStatus === st ? 'filled' : 'outlined'}
              onClick={() => setFilterStatus(st)}
              size="small"
              sx={{ fontWeight: 500 }}
            />
          ))}

          <Button
            variant="contained"
            size="small"
            startIcon={<PersonAddOutlinedIcon />}
            onClick={onOpenAddUser}
            sx={{ ml: 1 }}
          >
            Add Developer
          </Button>
        </Box>
      </Box>

      {/* Main Table */}
      <TableContainer>
        <Table sx={{ minWidth: 800 }}>
          <TableHead sx={{ backgroundColor: '#f1f3f4' }}>
            <TableRow>
              <TableCell>
                <TableSortLabel
                  active={sortField === 'email'}
                  direction={sortField === 'email' ? sortOrder : 'asc'}
                  onClick={() => handleSort('email')}
                >
                  Developer
                </TableSortLabel>
              </TableCell>
              <TableCell>Licensed Project &amp; Tier</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Weekly Reset</TableCell>
              <TableCell align="right">
                <TableSortLabel
                  active={sortField === 'tokens'}
                  direction={sortField === 'tokens' ? sortOrder : 'desc'}
                  onClick={() => handleSort('tokens')}
                >
                  Cycle Tokens
                </TableSortLabel>
              </TableCell>
              <TableCell align="right">
                <TableSortLabel
                  active={sortField === 'gross_spend'}
                  direction={sortField === 'gross_spend' ? sortOrder : 'desc'}
                  onClick={() => handleSort('gross_spend')}
                >
                  Gross Usage
                </TableSortLabel>
              </TableCell>
              <TableCell align="right">Quota Credits</TableCell>
              <TableCell align="right">
                <TableSortLabel
                  active={sortField === 'remaining_credit'}
                  direction={sortField === 'remaining_credit' ? sortOrder : 'desc'}
                  onClick={() => handleSort('remaining_credit')}
                >
                  Credit Balance / Net Overage
                </TableSortLabel>
              </TableCell>
              <TableCell sx={{ minWidth: 160 }}>
                <TableSortLabel
                  active={sortField === 'utilization'}
                  direction={sortField === 'utilization' ? sortOrder : 'desc'}
                  onClick={() => handleSort('utilization')}
                >
                  Credit Utilization
                </TableSortLabel>
              </TableCell>
              <TableCell align="right">Last Active</TableCell>
              <TableCell align="center">Actions</TableCell>
            </TableRow>
          </TableHead>

          <TableBody>
            {filteredUsers.length === 0 ? (
              <TableRow>
                <TableCell colSpan={11} align="center" sx={{ py: 4, color: '#5f6368' }}>
                  No developers found matching current filters.
                </TableCell>
              </TableRow>
            ) : (
              filteredUsers.map((user) => {
                const util = user.current_week.credit_utilization_percentage;
                const utilColor = getUtilizationColor(util);
                const hasOverage = user.current_week.net_billable_cost_usd > 0;
                const userProject = user.project_id || defaultProject;
                const rowPool = projectPools?.find((p) => p.project_id === userProject);
                const effectiveTier = user.license_tier || rowPool?.license_tier || 'ENTERPRISE_PLUS';
                const isStdTier = effectiveTier === 'ENTERPRISE_STANDARD';
                const isStd = isStdTier;
                const resetInfo = formatWeeklyReset(
                  user.current_week.wtu_window_reset_at,
                  rowPool?.cycle_reset_utc || defaultResetUtc
                );

                return (
                  <TableRow
                    key={user.email}
                    hover
                    onClick={() => onSelectUser(user)}
                    sx={{
                      cursor: 'pointer',
                      '&:hover': { backgroundColor: '#f8f9fa' },
                    }}
                  >
                    {/* User & Email */}
                    <TableCell>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                        <Avatar
                          sx={{
                            width: 32,
                            height: 32,
                            bgcolor: isStd ? '#7b1fa2' : '#1a73e8',
                            fontSize: 14,
                            fontWeight: 600,
                          }}
                        >
                          {user.email.charAt(0).toUpperCase()}
                        </Avatar>
                        <Box>
                          <Typography variant="body2" sx={{ fontWeight: 600, color: '#202124' }}>
                            {user.email}
                          </Typography>
                          {user.has_custom_quota && (
                            <Typography variant="caption" sx={{ color: '#1a73e8', fontWeight: 500 }}>
                              Custom Quota
                            </Typography>
                          )}
                        </Box>
                      </Box>
                    </TableCell>

                    {/* Licensed Project & Tier */}
                    <TableCell>
                      <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 0.4 }}>
                        <Typography
                          variant="caption"
                          sx={{
                            fontFamily: 'monospace',
                            fontWeight: 600,
                            color: '#3c4043',
                            bgcolor: '#f1f3f4',
                            px: 0.8,
                            py: 0.2,
                            borderRadius: 1,
                          }}
                        >
                          {userProject}
                        </Typography>
                        <Chip
                          size="small"
                          label={isStd ? 'Enterprise Standard' : 'Enterprise Plus'}
                          sx={{
                            height: 18,
                            fontSize: 10,
                            fontWeight: 700,
                            bgcolor: isStd ? '#f3e8fd' : '#e8f0fe',
                            color: isStd ? '#7b1fa2' : '#1a73e8',
                          }}
                        />
                      </Box>
                    </TableCell>

                    {/* Status Chip */}
                    <TableCell>{getStatusChip(user)}</TableCell>

                    {/* Weekly Reset */}
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600, color: '#202124', fontSize: 12.5 }}>
                        {resetInfo.label}
                      </Typography>
                      <Typography
                        variant="caption"
                        sx={{
                          color: user.status === 'AUTO_DISABLED' ? '#d93025' : '#1a73e8',
                          fontWeight: 600,
                          display: 'block',
                        }}
                      >
                        {user.status === 'MANUALLY_DISABLED'
                          ? 'Locked across resets'
                          : resetInfo.countdown
                          ? user.status === 'AUTO_DISABLED'
                            ? `Auto-Restores ${resetInfo.countdown}`
                            : `Resets ${resetInfo.countdown}`
                          : 'Sliding window'}
                      </Typography>
                    </TableCell>

                    {/* Tokens */}
                    <TableCell align="right">
                      <Typography variant="body2" sx={{ fontWeight: 500, fontFamily: 'monospace' }}>
                        {user.current_week.total_tokens.toLocaleString()}
                      </Typography>
                    </TableCell>

                    {/* Gross Spend */}
                    <TableCell align="right">
                      <Typography variant="body2" sx={{ fontWeight: 600, color: '#202124' }}>
                        ${user.current_week.gross_spend_usd.toFixed(2)}
                      </Typography>
                    </TableCell>

                    {/* Quota Credits */}
                    <TableCell align="right">
                      <Typography variant="body2" sx={{ color: '#5f6368' }}>
                        ${user.current_week.quota_credits_usd.toFixed(2)}
                      </Typography>
                    </TableCell>

                    {/* Balance / Net Overage */}
                    <TableCell align="right">
                      {hasOverage ? (
                        <Typography variant="body2" sx={{ fontWeight: 700, color: '#d93025' }}>
                          +${user.current_week.net_billable_cost_usd.toFixed(2)} Net Overage
                        </Typography>
                      ) : (
                        <Typography variant="body2" sx={{ fontWeight: 600, color: '#188038' }}>
                          ${user.current_week.remaining_credit_usd.toFixed(2)} Left
                        </Typography>
                      )}
                    </TableCell>

                    {/* Utilization Progress Bar */}
                    <TableCell>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Box sx={{ flexGrow: 1 }}>
                          <LinearProgress
                            variant="determinate"
                            value={Math.min(100, util)}
                            sx={{
                              height: 8,
                              borderRadius: 4,
                              backgroundColor: '#e8eaed',
                              '& .MuiLinearProgress-bar': {
                                backgroundColor: utilColor,
                                borderRadius: 4,
                              },
                            }}
                          />
                        </Box>
                        <Typography variant="caption" sx={{ minWidth: 42, fontWeight: 600, color: utilColor }}>
                          {util.toFixed(0)}%
                        </Typography>
                      </Box>
                    </TableCell>

                    {/* Last Active */}
                    <TableCell align="right">
                      <Typography variant="caption" sx={{ color: '#70757a' }}>
                        {formatRelativeTime(user.current_week.last_active)}
                      </Typography>
                    </TableCell>

                    {/* Quick Action Button */}
                    <TableCell align="center" onClick={(e) => e.stopPropagation()}>
                      <IconButton
                        size="small"
                        disabled={pendingUserEmail === user.email}
                        onClick={(e) => handleOpenMenu(e, user)}
                      >
                        <MoreVertIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* Row Action Menu */}
      <Menu
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={handleCloseMenu}
        transformOrigin={{ horizontal: 'right', vertical: 'top' }}
        anchorOrigin={{ horizontal: 'right', vertical: 'bottom' }}
      >
        <MenuItem
          onClick={() => {
            if (activeMenuUser) onSelectUser(activeMenuUser);
            handleCloseMenu();
          }}
        >
          <EditOutlinedIcon fontSize="small" sx={{ mr: 1, color: '#1a73e8' }} />
          Edit Quota & Settings
        </MenuItem>
        <MenuItem
          disabled={Boolean(activeMenuUser && pendingUserEmail === activeMenuUser.email)}
          onClick={() => {
            if (activeMenuUser) onToggleLock(activeMenuUser.email);
            handleCloseMenu();
          }}
        >
          {activeMenuUser?.status === 'MANUALLY_DISABLED' ? (
            <>
              <LockOpenIcon fontSize="small" sx={{ mr: 1, color: '#188038' }} />
              Unlock Access
            </>
          ) : (
            <>
              <LockIcon fontSize="small" sx={{ mr: 1, color: '#d93025' }} />
              Lock / Disable Access
            </>
          )}
        </MenuItem>
        <MenuItem
          disabled={Boolean(activeMenuUser && pendingUserEmail === activeMenuUser.email)}
          onClick={() => {
            if (activeMenuUser) onToggleExempt(activeMenuUser);
            handleCloseMenu();
          }}
        >
          <VerifiedUserOutlinedIcon fontSize="small" sx={{ mr: 1, color: '#7b1fa2' }} />
          {activeMenuUser?.is_exempt ? 'Remove Exemption' : 'Set as Exempt'}
        </MenuItem>
      </Menu>
    </Paper>
  );
};
