import React from 'react';
import { Box, Card, CardContent, Chip, Grid, LinearProgress, Typography } from '@mui/material';
import PeopleAltOutlinedIcon from '@mui/icons-material/PeopleAltOutlined';
import BlockOutlinedIcon from '@mui/icons-material/BlockOutlined';
import TokenOutlinedIcon from '@mui/icons-material/TokenOutlined';
import AttachMoneyOutlinedIcon from '@mui/icons-material/AttachMoneyOutlined';
import AccountBalanceWalletOutlinedIcon from '@mui/icons-material/AccountBalanceWalletOutlined';
import WarningAmberOutlinedIcon from '@mui/icons-material/WarningAmberOutlined';
import AccessTimeOutlinedIcon from '@mui/icons-material/AccessTimeOutlined';
import SpeedOutlinedIcon from '@mui/icons-material/SpeedOutlined';
import CloudQueueOutlinedIcon from '@mui/icons-material/CloudQueueOutlined';
import { KPIStats, ProjectWTUPoolStatus, User } from '../types';

interface KPISummaryProps {
  kpis: KPIStats | null;
  users?: User[];
  selectedProject?: string;
  onSelectProject?: (projectId: string) => void;
}

export const KPISummary: React.FC<KPISummaryProps> = ({
  kpis,
  users,
  selectedProject = 'ALL',
  onSelectProject,
}) => {
  const formatTokens = (tokens: number) => {
    if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
    if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}k`;
    return tokens.toLocaleString();
  };

  const formatResetDate = (isoStr?: string | null) => {
    if (!isoStr) return '7-Day Sliding Window';
    try {
      const d = new Date(isoStr);
      return d.toLocaleString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoStr;
    }
  };

  const formatShortDate = (isoStr?: string | null) => {
    if (!isoStr) return 'Active Cycle';
    try {
      const d = new Date(isoStr);
      return d.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoStr;
    }
  };

  const projectPools: ProjectWTUPoolStatus[] = kpis?.project_pools || [];
  const monitoredProjects: string[] = kpis?.monitored_projects || projectPools.map((p) => p.project_id);
  const defaultProject = monitoredProjects[0] || 'your-workload-project';
  const visiblePools =
    selectedProject === 'ALL'
      ? projectPools
      : projectPools.filter((p) => p.project_id === selectedProject);

  const scopedUsers =
    users && selectedProject !== 'ALL'
      ? users.filter((u) => (u.project_id || defaultProject) === selectedProject)
      : null;

  const activeDevs = scopedUsers
    ? scopedUsers.filter((u) => u.status === 'ACTIVE' && !u.is_exempt).length
    : kpis?.active_developers;
  const exemptDevs = scopedUsers
    ? scopedUsers.filter((u) => u.is_exempt && u.status !== 'MANUALLY_DISABLED').length
    : kpis?.exempt_developers || 0;
  const throttledDevs = scopedUsers
    ? scopedUsers.filter((u) => u.status === 'AUTO_DISABLED').length
    : kpis?.auto_throttled_developers;
  const lockedDevs = scopedUsers
    ? scopedUsers.filter((u) => u.status === 'MANUALLY_DISABLED').length
    : kpis?.manually_locked_developers || 0;
  const cycleTokens = scopedUsers
    ? scopedUsers.reduce((acc, u) => acc + u.current_week.total_tokens, 0)
    : kpis?.total_tokens_this_week;
  const cycleGross = scopedUsers
    ? scopedUsers.reduce((acc, u) => acc + u.current_week.gross_spend_usd, 0)
    : kpis?.total_gross_usage_usd;
  const cycleCredits = scopedUsers
    ? scopedUsers.reduce((acc, u) => acc + u.current_week.quota_credits_usd, 0)
    : kpis?.total_credits_allocated_usd;
  const cycleNet = scopedUsers
    ? scopedUsers.reduce((acc, u) => acc + u.current_week.net_billable_cost_usd, 0)
    : kpis?.total_net_overages_usd;

  const isProjectFiltered = selectedProject !== 'ALL';
  const grossUsd = isProjectFiltered ? (cycleGross ?? 0) : (kpis?.total_gross_usage_usd ?? 0);
  const defaultBudget = kpis?.weekly_team_budget_usd ?? kpis?.total_credits_allocated_usd ?? 100;
  const budgetUsd = isProjectFiltered ? (cycleCredits ?? 0) : defaultBudget;
  const budgetPct = isProjectFiltered
    ? (budgetUsd > 0 ? (grossUsd / budgetUsd) * 100 : 0)
    : (kpis?.weekly_budget_utilization_pct ?? (budgetUsd > 0 ? (grossUsd / budgetUsd) * 100 : 0));

  const cards = [
    {
      title: 'Active Developers',
      value: kpis ? (activeDevs ?? '-') : '-',
      subtext: `${exemptDevs} Exempt`,
      icon: <PeopleAltOutlinedIcon sx={{ color: '#188038' }} />,
      bgColor: '#e6f4ea',
    },
    {
      title: 'Auto-Throttled',
      value: kpis ? (throttledDevs ?? '-') : '-',
      subtext: `${lockedDevs} Locked`,
      icon: <BlockOutlinedIcon sx={{ color: '#d93025' }} />,
      bgColor: '#fce8e6',
    },
    {
      title: 'Tokens This Cycle',
      value: kpis && cycleTokens !== undefined ? formatTokens(cycleTokens) : '-',
      subtext: selectedProject === 'ALL' ? 'Across Monitored Projects' : selectedProject,
      icon: <TokenOutlinedIcon sx={{ color: '#1a73e8' }} />,
      bgColor: '#e8f0fe',
    },
    {
      title: 'Total Gross Usage',
      value: kpis && cycleGross !== undefined ? `$${cycleGross.toFixed(2)}` : '-',
      subtext: 'Actual Token Spend',
      icon: <AttachMoneyOutlinedIcon sx={{ color: '#e37400' }} />,
      bgColor: '#fef7e0',
    },
    {
      title: 'Credits Allocated',
      value: kpis && cycleCredits !== undefined ? `$${cycleCredits.toFixed(2)}` : '-',
      subtext: 'Granted Allowance',
      icon: <AccountBalanceWalletOutlinedIcon sx={{ color: '#1a73e8' }} />,
      bgColor: '#e8f0fe',
    },
    {
      title: 'Net Overages',
      value: kpis && cycleNet !== undefined ? `$${cycleNet.toFixed(2)}` : '-',
      subtext: 'Above Allocated Quota',
      icon: <WarningAmberOutlinedIcon sx={{ color: '#d93025' }} />,
      bgColor: '#fce8e6',
    },
  ];

  return (
    <Box sx={{ mb: 3 }}>
      {/* Workload Project Filter Bar */}
      {monitoredProjects.length > 0 && onSelectProject && (
        <Card
          elevation={0}
          sx={{
            mb: 2,
            px: 2,
            py: 1.2,
            border: '1px solid #dadce0',
            borderRadius: 2,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 1.5,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography variant="caption" sx={{ color: '#5f6368', fontWeight: 700, textTransform: 'uppercase', mr: 0.5 }}>
              Monitored Workload Projects:
            </Typography>
            <Chip
              size="small"
              label={`All Projects (${monitoredProjects.length})`}
              clickable
              color={selectedProject === 'ALL' ? 'primary' : 'default'}
              variant={selectedProject === 'ALL' ? 'filled' : 'outlined'}
              onClick={() => onSelectProject('ALL')}
              sx={{ fontWeight: 600 }}
            />
            {projectPools.map((pool) => {
              const isStd = pool.license_tier === 'ENTERPRISE_STANDARD';
              const isSelected = selectedProject === pool.project_id;
              return (
                <Chip
                  key={pool.project_id}
                  size="small"
                  clickable
                  onClick={() => onSelectProject(pool.project_id)}
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.8 }}>
                      <span>{pool.project_id}</span>
                      <Box
                        component="span"
                        sx={{
                          px: 0.7,
                          py: 0.1,
                          borderRadius: 1,
                          fontSize: 10,
                          fontWeight: 700,
                          bgcolor: isStd ? '#f3e8fd' : '#e8f0fe',
                          color: isStd ? '#7b1fa2' : '#1a73e8',
                        }}
                      >
                        {isStd ? 'Enterprise Standard' : 'Enterprise Plus'}
                      </Box>
                    </Box>
                  }
                  variant={isSelected ? 'filled' : 'outlined'}
                  sx={{
                    fontWeight: 600,
                    borderColor: isSelected ? '#1a73e8' : '#dadce0',
                    bgcolor: isSelected ? '#e8f0fe' : '#ffffff',
                    color: '#202124',
                  }}
                />
              );
            })}
          </Box>
          <Typography variant="caption" sx={{ color: '#5f6368', fontWeight: 500 }}>
            1-to-1 Developer License Mapping • Weekly Reset Locked to Project WTU Cycle
          </Typography>
        </Card>
      )}

      {/* Per-Project WTU Pool & Weekly Reset Cards + Team Quota Progress */}
      <Grid container spacing={2} sx={{ mb: 2 }}>
        {visiblePools.map((pool) => {
          const isStd = pool.license_tier === 'ENTERPRISE_STANDARD';
          const pct = pool.wtu_utilization_pct || 0;
          const daysInt = Math.floor(pool.days_until_reset || 0);
          return (
            <Grid item xs={12} md={visiblePools.length === 1 ? 7 : 4} key={pool.project_id}>
              <Card
                elevation={0}
                sx={{
                  border: '1px solid #dadce0',
                  borderLeft: `4px solid ${isStd ? '#7b1fa2' : '#1a73e8'}`,
                  borderRadius: 2,
                  height: '100%',
                }}
              >
                <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 0.75 }}>
                    <Box sx={{ minWidth: 0, flex: 1, mr: 1.5 }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                        <CloudQueueOutlinedIcon sx={{ fontSize: 18, color: isStd ? '#7b1fa2' : '#1a73e8', flexShrink: 0 }} />
                        <Typography variant="subtitle2" sx={{ fontWeight: 700, color: '#202124', whiteSpace: 'nowrap' }}>
                          {pool.project_id}
                        </Typography>
                      </Box>
                      <Box sx={{ mt: 0.5 }}>
                        <Chip
                          size="small"
                          label={isStd ? 'Enterprise Standard' : 'Enterprise Plus'}
                          sx={{
                            height: 20,
                            fontSize: 10,
                            fontWeight: 700,
                            bgcolor: isStd ? '#f3e8fd' : '#e8f0fe',
                            color: isStd ? '#7b1fa2' : '#1a73e8',
                          }}
                        />
                      </Box>
                    </Box>

                    {/* Weekly Reset Badge */}
                    <Box
                      sx={{
                        textAlign: 'right',
                        bgcolor: '#f8fafc',
                        px: 1.25,
                        py: 0.5,
                        borderRadius: 1,
                        border: '1px solid #e2e8f0',
                        flexShrink: 0,
                        whiteSpace: 'nowrap',
                      }}
                    >
                      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 0.5 }}>
                        <AccessTimeOutlinedIcon sx={{ fontSize: 13, color: isStd ? '#7b1fa2' : '#1a73e8' }} />
                        <Typography
                          variant="caption"
                          sx={{
                            fontSize: 10,
                            fontWeight: 700,
                            textTransform: 'uppercase',
                            color: isStd ? '#7b1fa2' : '#1a73e8',
                          }}
                        >
                          Weekly Reset
                        </Typography>
                      </Box>
                      <Typography variant="caption" sx={{ fontWeight: 700, color: '#202124', display: 'block' }}>
                        {formatResetDate(pool.cycle_reset_utc)} (in {daysInt}d {pool.hours_until_reset ?? 0}h)
                      </Typography>
                    </Box>
                  </Box>
                  <Typography
                    variant="caption"
                    sx={{
                      fontFamily: 'monospace',
                      color: '#475569',
                      fontSize: '0.68rem',
                      mb: 1,
                      display: 'block',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {pool.tier_metric || 'agentic_wtu_tier_enterprise_plus_pp_weekly_regional'}
                  </Typography>

                  {/* WTU Pool Progress */}
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.5 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                      <SpeedOutlinedIcon sx={{ fontSize: 15, color: '#5f6368' }} />
                      <Typography variant="caption" sx={{ color: '#5f6368', fontWeight: 600 }}>
                        Shared Project WTU Pool
                      </Typography>
                    </Box>
                    <Typography variant="body2" sx={{ fontWeight: 700, color: '#202124' }}>
                      {pool.wtu_consumed.toLocaleString()}{' '}
                      {pool.wtu_limit > 0
                        ? `/ ${pool.wtu_limit.toLocaleString()} WTU (${pct.toFixed(1)}%)`
                        : 'WTU Consumed'}
                    </Typography>
                  </Box>
                  <LinearProgress
                    variant="determinate"
                    value={Math.min(100, Math.max(0, pct))}
                    sx={{
                      height: 8,
                      borderRadius: 4,
                      bgcolor: '#e8eaed',
                      mb: 1,
                      '& .MuiLinearProgress-bar': {
                        bgcolor: pct >= 90 ? '#d93025' : pct >= 75 ? '#e37400' : isStd ? '#7b1fa2' : '#1a73e8',
                      },
                    }}
                  />

                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 1 }}>
                    <Typography variant="caption" sx={{ color: '#5f6368' }}>
                      Cycle Started: <strong>{formatShortDate(pool.cycle_start_utc)}</strong>
                    </Typography>
                    <Typography variant="caption" sx={{ color: '#5f6368' }}>
                      <strong>{pool.active_developers}</strong> Active •{' '}
                      <strong style={{ color: pool.throttled_developers > 0 ? '#d93025' : 'inherit' }}>
                        {pool.throttled_developers} Throttled
                      </strong>{' '}
                      • Cycle Spend: <strong>${pool.cycle_spend_usd.toFixed(2)}</strong>
                    </Typography>
                  </Box>
                </CardContent>
              </Card>
            </Grid>
          );
        })}

        {/* Team USD Budget Card */}
        <Grid item xs={12} md={visiblePools.length === 1 ? 5 : visiblePools.length >= 2 ? 4 : 12}>
          <Card
            elevation={0}
            sx={{
              border: '1px solid #dadce0',
              borderLeft: '4px solid #188038',
              borderRadius: 2,
              height: '100%',
              background: 'linear-gradient(90deg, #ffffff 0%, #f8fbff 100%)',
            }}
          >
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                <Typography variant="caption" sx={{ color: '#5f6368', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  {isProjectFiltered ? `${selectedProject} FinOps Credit Pool` : 'Team FinOps Credit Pool'}
                </Typography>
                <Chip
                  size="small"
                  label={isProjectFiltered ? 'Project Scoped' : 'WTU-Cycle Aligned'}
                  sx={{ height: 20, fontSize: 10, fontWeight: 700, bgcolor: '#e6f4ea', color: '#188038' }}
                />
              </Box>

              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', mb: 0.8 }}>
                <Typography variant="body2" sx={{ color: '#5f6368', fontWeight: 600 }}>
                  {isProjectFiltered ? 'Project Cycle Spend' : 'Aggregate Cycle Spend'}
                </Typography>
                <Typography variant="body1" sx={{ fontWeight: 700, color: '#202124' }}>
                  ${grossUsd.toFixed(2)} / ${budgetUsd.toFixed(2)} ({budgetPct.toFixed(1)}%)
                </Typography>
              </Box>

              <LinearProgress
                variant="determinate"
                value={Math.min(100, Math.max(0, budgetPct))}
                sx={{
                  height: 8,
                  borderRadius: 4,
                  bgcolor: '#e8eaed',
                  mb: 1,
                  '& .MuiLinearProgress-bar': {
                    bgcolor: budgetPct >= 90 ? '#d93025' : budgetPct >= 75 ? '#e37400' : '#188038',
                  },
                }}
              />

              <Typography variant="caption" sx={{ color: '#5f6368', display: 'block', lineHeight: 1.4 }}>
                Auto-throttled developers are automatically restored to <strong>antigravity-enabled@</strong> at their project&apos;s <strong>Weekly Reset</strong>.
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* KPI Cards */}
      <Grid container spacing={2}>
        {cards.map((card, idx) => (
          <Grid item xs={12} sm={6} md={2} key={idx}>
            <Card
              elevation={0}
              sx={{
                border: '1px solid #dadce0',
                borderRadius: 2,
                height: '100%',
                transition: 'transform 0.15s ease-in-out, box-shadow 0.15s ease-in-out',
                '&:hover': {
                  boxShadow: '0 2px 6px rgba(60,64,67,0.15)',
                  transform: 'translateY(-1px)',
                },
              }}
            >
              <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
                  <Typography variant="caption" sx={{ color: '#5f6368', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    {card.title}
                  </Typography>
                  <Box
                    sx={{
                      width: 32,
                      height: 32,
                      borderRadius: '50%',
                      backgroundColor: card.bgColor,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    {card.icon}
                  </Box>
                </Box>
                <Typography variant="h5" sx={{ fontWeight: 700, color: '#202124' }}>
                  {card.value}
                </Typography>
                <Typography variant="caption" sx={{ color: '#70757a', mt: 0.5, display: 'block' }}>
                  {card.subtext}
                </Typography>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>
    </Box>
  );
};
