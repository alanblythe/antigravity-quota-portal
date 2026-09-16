import React, { useEffect, useState } from 'react';
import {
  Box,
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
import { AppConfig } from '../types';

interface SettingsModalProps {
  config: AppConfig | null;
  open: boolean;
  onClose: () => void;
  onSave: (payload: Partial<AppConfig>) => Promise<void>;
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
  const [webhookUrl, setWebhookUrl] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config) {
      setTimezone(config.timezone);
      setEnabledGroup(config.enabled_group);
      setDisabledGroup(config.disabled_group);
      setDefaultQuota(config.default_quota_usd.toFixed(2));
      setDefaultOverage(config.default_overage_usd.toFixed(2));
      setWebhookUrl(config.webhook_alert_url || '');
    }
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave({
        timezone,
        enabled_group: enabledGroup,
        disabled_group: disabledGroup,
        default_quota_usd: parseFloat(defaultQuota) || 10.0,
        default_overage_usd: parseFloat(defaultOverage) || 2.0,
        webhook_alert_url: webhookUrl,
      });
      onClose();
    } catch (e) {
      console.error(e);
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
        <Grid container spacing={2.5}>
          <Grid item xs={12}>
            <Typography variant="caption" sx={{ color: '#5f6368', fontWeight: 600 }}>
              TIMEZONE & BUDGET BOUNDARY
            </Typography>
            <TextField
              select
              fullWidth
              size="small"
              label="Application Timezone (Monday 00:00 Reset)"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              sx={{ mt: 1 }}
            >
              {COMMON_TIMEZONES.map((tz) => (
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
              DEFAULT BUDGET CREDITS & OVERAGES
            </Typography>
            <Grid container spacing={2} sx={{ mt: 0.2 }}>
              <Grid item xs={6}>
                <TextField
                  fullWidth
                  size="small"
                  label="Default Weekly Quota"
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
