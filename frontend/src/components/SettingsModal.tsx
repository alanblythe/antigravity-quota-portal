import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Grid,
  InputAdornment,
  MenuItem,
  TextField,
  Typography,
} from '@mui/material';
import SettingsIcon from '@mui/icons-material/Settings';
import { AppConfig, ConfigUpdatePayload } from '../types';

interface SettingsModalProps {
  config: AppConfig | null;
  open: boolean;
  onClose: () => void;
  onSave: (payload: ConfigUpdatePayload) => Promise<void>;
}

const COMMON_TIMEZONES = [
  'America/Los_Angeles',
  'America/Denver',
  'America/Chicago',
  'America/New_York',
  'UTC',
  'Europe/London',
  'Europe/Paris',
  'Asia/Tokyo',
  'Asia/Singapore',
  'Australia/Sydney',
];

const WTU_TIER_OPTIONS = [
  {
    value: 'agentic_wtu_tier_enterprise_plus_pp_weekly_regional',
    label: 'Enterprise Plus — Per-Project Pool (pp)',
  },
  {
    value: 'agentic_wtu_tier_enterprise_standard_pp_weekly_regional',
    label: 'Enterprise Standard — Per-Project Pool (pp)',
  },
  {
    value: 'agentic_wtu_tier_enterprise_plus_pppu_weekly_regional',
    label: 'Enterprise Plus — Per-Project Per-User (pppu)',
  },
  {
    value: 'agentic_wtu_tier_enterprise_standard_pppu_weekly_regional',
    label: 'Enterprise Standard — Per-Project Per-User (pppu)',
  },
  {
    value: 'agentic_wtu_tier_enterprise_plus_ppu_weekly_regional',
    label: 'Enterprise Plus — Per-User Legacy (ppu)',
  },
  {
    value: 'agentic_wtu_tier_enterprise_standard_ppu_weekly_regional',
    label: 'Enterprise Standard — Per-User Legacy (ppu)',
  },
];

export const SettingsModal: React.FC<SettingsModalProps> = ({
  config,
  open,
  onClose,
  onSave,
}) => {
  const [timezone, setTimezone] = useState('America/Los_Angeles');
  const [enabledGroup, setEnabledGroup] = useState('');
  const [disabledGroup, setDisabledGroup] = useState('');
  const [defaultQuota, setDefaultQuota] = useState('10.00');
  const [defaultOverage, setDefaultOverage] = useState('2.00');
  const [weeklyTeamBudget, setWeeklyTeamBudget] = useState('100.00');
  const [weeklyWtuQuota, setWeeklyWtuQuota] = useState('0');
  const [wtuTierMetric, setWtuTierMetric] = useState('agentic_wtu_tier_enterprise_plus_pp_weekly_regional');
  const [monitoredProjectsInput, setMonitoredProjectsInput] = useState('your-workload-project');
  const [webhookUrl, setWebhookUrl] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prevOpenRef = useRef(false);

  useEffect(() => {
    if (open && !prevOpenRef.current && config) {
      setTimezone(config.timezone);
      setEnabledGroup(config.enabled_group);
      setDisabledGroup(config.disabled_group);
      setDefaultQuota(config.default_quota_usd.toFixed(2));
      setDefaultOverage(config.default_overage_usd.toFixed(2));
      setWeeklyTeamBudget((config.weekly_team_budget_usd ?? 100.0).toFixed(2));
      setWeeklyWtuQuota(String(config.weekly_wtu_quota ?? 0));
      setWtuTierMetric(config.wtu_tier_metric || 'agentic_wtu_tier_enterprise_plus_pp_weekly_regional');
      setMonitoredProjectsInput((config.monitored_projects || ['your-workload-project']).join(', '));
      setWebhookUrl(config.webhook_alert_url || '');
      setError(null);
    }
    prevOpenRef.current = open;
  }, [config, open]);

  const parseNonNegativeFloat = (val: string, fallback: number): number => {
    const n = parseFloat(val);
    return Number.isFinite(n) && n >= 0 ? n : fallback;
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const parsedProjects = monitoredProjectsInput
        .split(',')
        .map((p) => p.trim())
        .filter(Boolean);
      await onSave({
        timezone,
        enabled_group: enabledGroup,
        disabled_group: disabledGroup,
        default_quota_usd: parseNonNegativeFloat(defaultQuota, 10.0),
        default_overage_usd: parseNonNegativeFloat(defaultOverage, 2.0),
        weekly_team_budget_usd: parseNonNegativeFloat(weeklyTeamBudget, 100.0),
        weekly_wtu_quota: parseNonNegativeFloat(weeklyWtuQuota, 0.0),
        wtu_tier_metric: wtuTierMetric,
        monitored_projects: parsedProjects.length > 0 ? parsedProjects : undefined,
        webhook_alert_url: webhookUrl,
      });
      onClose();
    } catch (e: any) {
      console.error(e);
      setError(e?.message || 'Failed to save portal settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <SettingsIcon sx={{ color: '#1a73e8' }} />
        Portal & Governance Settings
      </DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Grid container spacing={2.5}>
          <Grid item xs={12}>
            <Typography variant="caption" sx={{ color: '#5f6368', fontWeight: 600 }}>
              MONITORED WORKLOAD PROJECTS &amp; TIMEZONE
            </Typography>
            <TextField
              fullWidth
              size="small"
              label="Monitored GCP Workload Project IDs (comma-separated)"
              value={monitoredProjectsInput}
              onChange={(e) => setMonitoredProjectsInput(e.target.value)}
              helperText="Cloud Monitoring WTU pools and BigQuery logs are tracked per workload project"
              sx={{ mt: 1, mb: 1.5 }}
            />
            <TextField
              select
              fullWidth
              size="small"
              label="Application Timezone (Weekly Reset Locked to Project WTU Cycle)"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
            >
              {(timezone && !COMMON_TIMEZONES.includes(timezone)
                ? [timezone, ...COMMON_TIMEZONES]
                : COMMON_TIMEZONES
              ).map((tz) => (
                <MenuItem key={tz} value={tz}>
                  {tz}
                </MenuItem>
              ))}
            </TextField>
          </Grid>

          <Grid item xs={12}>
            <Typography variant="caption" sx={{ color: '#5f6368', fontWeight: 600 }}>
              GOOGLE CLOUD IDENTITY GROUPS
            </Typography>
            <TextField
              fullWidth
              size="small"
              label="Active Developers Group Email"
              value={enabledGroup}
              onChange={(e) => setEnabledGroup(e.target.value)}
              helperText="Bound to roles/businessaicode.user in GCP IAM"
              sx={{ mt: 1, mb: 1.5 }}
            />
            <TextField
              fullWidth
              size="small"
              label="Throttled / Disabled Developers Group Email"
              value={disabledGroup}
              onChange={(e) => setDisabledGroup(e.target.value)}
              helperText="Zero GCP IAM permissions. Holds paused developers."
            />
          </Grid>

          <Grid item xs={12}>
            <Typography variant="caption" sx={{ color: '#5f6368', fontWeight: 600 }}>
              DEFAULT BUDGET CREDITS & TEAM WEEKLY QUOTA
            </Typography>
            <Grid container spacing={2} sx={{ mt: 0.2 }}>
              <Grid item xs={6}>
                <TextField
                  fullWidth
                  size="small"
                  label="Default Developer Weekly Quota"
                  value={defaultQuota}
                  onChange={(e) => setDefaultQuota(e.target.value)}
                  InputProps={{
                    startAdornment: <InputAdornment position="start">$</InputAdornment>,
                  }}
                />
              </Grid>
              <Grid item xs={6}>
                <TextField
                  fullWidth
                  size="small"
                  label="Default Overage Buffer"
                  value={defaultOverage}
                  onChange={(e) => setDefaultOverage(e.target.value)}
                  InputProps={{
                    startAdornment: <InputAdornment position="start">$</InputAdornment>,
                  }}
                />
              </Grid>
              <Grid item xs={6}>
                <TextField
                  fullWidth
                  size="small"
                  label="Total Weekly Team Quota Pool"
                  value={weeklyTeamBudget}
                  onChange={(e) => setWeeklyTeamBudget(e.target.value)}
                  helperText="Target weekly USD cap for progress bar"
                  InputProps={{
                    startAdornment: <InputAdornment position="start">$</InputAdornment>,
                  }}
                />
              </Grid>
              <Grid item xs={6}>
                <TextField
                  fullWidth
                  size="small"
                  label="Weekly WTU Quota Override"
                  value={weeklyWtuQuota}
                  onChange={(e) => setWeeklyWtuQuota(e.target.value)}
                  helperText="0 = auto-detect from Cloud Monitoring"
                />
              </Grid>
              <Grid item xs={12}>
                <TextField
                  select
                  fullWidth
                  size="small"
                  label="Preferred Cloud Monitoring WTU Tier Metric"
                  value={wtuTierMetric}
                  onChange={(e) => setWtuTierMetric(e.target.value)}
                  helperText="Primary Discovery Engine WTU quota metric (auto-falls back across all tiers)"
                >
                  {(wtuTierMetric && !WTU_TIER_OPTIONS.map((o) => o.value).includes(wtuTierMetric)
                    ? [{ value: wtuTierMetric, label: wtuTierMetric }, ...WTU_TIER_OPTIONS]
                    : WTU_TIER_OPTIONS
                  ).map((opt) => (
                    <MenuItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </MenuItem>
                  ))}
                </TextField>
              </Grid>
            </Grid>
          </Grid>

          <Grid item xs={12}>
            <Typography variant="caption" sx={{ color: '#5f6368', fontWeight: 600 }}>
              INTEGRATIONS & WEBHOOKS
            </Typography>
            <TextField
              fullWidth
              size="small"
              label="Google Chat / Slack Alert Webhook URL"
              value={webhookUrl}
              onChange={(e) => setWebhookUrl(e.target.value)}
              placeholder="https://chat.googleapis.com/v1/spaces/..."
              sx={{ mt: 1 }}
            />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button variant="contained" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save Settings'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
