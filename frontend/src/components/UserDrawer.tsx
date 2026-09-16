import React, { useEffect, useState } from 'react';
import {
  Avatar,
  Box,
  Button,
  Divider,
  Drawer,
  FormControl,
  FormControlLabel,
  FormLabel,
  Grid,
  IconButton,
  InputAdornment,
  LinearProgress,
  Paper,
  Radio,
  RadioGroup,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import SaveIcon from '@mui/icons-material/Save';
import LockIcon from '@mui/icons-material/Lock';
import LockOpenIcon from '@mui/icons-material/LockOpen';
import VerifiedUserIcon from '@mui/icons-material/VerifiedUser';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import LayersIcon from '@mui/icons-material/Layers';
import { AppConfig, User, UserStatus, UserUpdatePayload } from '../types';

interface UserDrawerProps {
  user: User | null;
  config: AppConfig | null;
  open: boolean;
  onClose: () => void;
  onSave: (email: string, payload: UserUpdatePayload) => Promise<void>;
}

export const UserDrawer: React.FC<UserDrawerProps> = ({
  user,
  config,
  open,
  onClose,
  onSave,
}) => {
  const [hasCustomQuota, setHasCustomQuota] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState<string>('10');
  const [customQuotaInput, setCustomQuotaInput] = useState<string>('10.00');
  const [customOverageInput, setCustomOverageInput] = useState<string>('2.00');
  const [isExempt, setIsExempt] = useState(false);
  const [isLocked, setIsLocked] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (user && config) {
      setHasCustomQuota(user.has_custom_quota);
      const quotaVal = user.custom_quota_usd ?? config.default_quota_usd;
      const overageVal = user.custom_overage_usd ?? config.default_overage_usd;

      setCustomQuotaInput(quotaVal.toFixed(2));
      setCustomOverageInput(overageVal.toFixed(2));

      // Match preset or set custom
      if (config.preset_quotas.includes(quotaVal)) {
        setSelectedPreset(String(quotaVal));
      } else {
        setSelectedPreset('custom');
      }

      setIsExempt(user.is_exempt);
      setIsLocked(user.status === 'MANUALLY_DISABLED');
    }
  }, [user, config]);

  if (!user) return null;

  const handlePresetChange = (val: string) => {
    setSelectedPreset(val);
    if (val !== 'custom') {
      setCustomQuotaInput(parseFloat(val).toFixed(2));
    }
  };

  const handleSaveClick = async () => {
    setSaving(true);
    try {
      const quotaNum = parseFloat(customQuotaInput) || config?.default_quota_usd || 10.0;
      const overageNum = parseFloat(customOverageInput) || config?.default_overage_usd || 2.0;

      const payload: UserUpdatePayload = {
        has_custom_quota: hasCustomQuota,
        custom_quota_usd: hasCustomQuota ? quotaNum : null,
        custom_overage_usd: hasCustomQuota ? overageNum : null,
        is_exempt: isExempt,
        status: isLocked ? 'MANUALLY_DISABLED' : (user.status === 'MANUALLY_DISABLED' ? 'ACTIVE' : user.status),
      };

      await onSave(user.email, payload);
      onClose();
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  };

  const currentUsage = user.current_week;
  const breakdown = currentUsage.spend_breakdown;

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: { xs: '100%', sm: 520 },
          p: 0,
          backgroundColor: '#f8f9fa',
        },
      }}
    >
      {/* Header */}
      <Box
        sx={{
          p: 2.5,
          backgroundColor: '#ffffff',
          borderBottom: '1px solid #dadce0',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Avatar sx={{ bgcolor: '#1a73e8', width: 44, height: 44, fontWeight: 700 }}>
            {user.email.charAt(0).toUpperCase()}
          </Avatar>
          <Box>
            <Typography variant="h6" sx={{ color: '#202124', fontSize: 17 }}>
              {user.email}
            </Typography>
            <Typography variant="caption" sx={{ color: '#5f6368' }}>
              Status: <strong>{user.status}</strong> {user.is_exempt && '• (Exempt)'}
            </Typography>
          </Box>
        </Box>
        <IconButton onClick={onClose} size="small">
          <CloseIcon />
        </IconButton>
      </Box>

      {/* Drawer Content */}
      <Box sx={{ p: 3, overflowY: 'auto', flexGrow: 1 }}>
        {/* Financial Ledger Section */}
        <Typography variant="subtitle2" sx={{ mb: 1.5, display: 'flex', alignItems: 'center', gap: 1 }}>
          <AccountBalanceWalletIcon fontSize="small" sx={{ color: '#1a73e8' }} />
          Financial & Quota Ledger ({currentUsage.week_id})
        </Typography>

        <Grid container spacing={1.5} sx={{ mb: 3 }}>
          <Grid item xs={6}>
            <Paper elevation={0} sx={{ p: 1.5, border: '1px solid #dadce0', bgcolor: '#ffffff' }}>
              <Typography variant="caption" sx={{ color: '#5f6368' }}>
                Gross Token Value
              </Typography>
              <Typography variant="h6" sx={{ fontWeight: 700, color: '#202124' }}>
                ${currentUsage.gross_spend_usd.toFixed(2)}
              </Typography>
              <Typography variant="caption" sx={{ color: '#70757a' }}>
                {currentUsage.total_tokens.toLocaleString()} tokens
              </Typography>
            </Paper>
          </Grid>
          <Grid item xs={6}>
            <Paper elevation={0} sx={{ p: 1.5, border: '1px solid #dadce0', bgcolor: '#ffffff' }}>
              <Typography variant="caption" sx={{ color: '#5f6368' }}>
                Allocated Credits
              </Typography>
              <Typography variant="h6" sx={{ fontWeight: 700, color: '#1a73e8' }}>
                ${currentUsage.quota_credits_usd.toFixed(2)}
              </Typography>
              <Typography variant="caption" sx={{ color: '#70757a' }}>
                +${currentUsage.overage_buffer_usd.toFixed(2)} Buffer
              </Typography>
            </Paper>
          </Grid>
          <Grid item xs={6}>
            <Paper elevation={0} sx={{ p: 1.5, border: '1px solid #dadce0', bgcolor: '#ffffff' }}>
              <Typography variant="caption" sx={{ color: '#5f6368' }}>
                Remaining Balance
              </Typography>
              <Typography variant="h6" sx={{ fontWeight: 700, color: '#188038' }}>
                ${currentUsage.remaining_credit_usd.toFixed(2)}
              </Typography>
              <Typography variant="caption" sx={{ color: '#70757a' }}>
                Available credit
              </Typography>
            </Paper>
          </Grid>
          <Grid item xs={6}>
            <Paper elevation={0} sx={{ p: 1.5, border: '1px solid #dadce0', bgcolor: '#ffffff' }}>
              <Typography variant="caption" sx={{ color: '#5f6368' }}>
                Net Quota Overage
              </Typography>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 700,
                  color: currentUsage.net_billable_cost_usd > 0 ? '#d93025' : '#202124',
                }}
              >
                ${currentUsage.net_billable_cost_usd.toFixed(2)}
              </Typography>
              <Typography variant="caption" sx={{ color: '#70757a' }}>
                Net spend above quota
              </Typography>
            </Paper>
          </Grid>
        </Grid>

        {/* Model Breakdown */}
        <Typography variant="subtitle2" sx={{ mb: 1.5, display: 'flex', alignItems: 'center', gap: 1 }}>
          <LayersIcon fontSize="small" sx={{ color: '#1a73e8' }} />
          Model Token & Spend Breakdown
        </Typography>
        <Paper elevation={0} sx={{ p: 2, mb: 3, border: '1px solid #dadce0', bgcolor: '#ffffff' }}>
          {Object.keys(currentUsage.tokens_by_model).length === 0 ? (
            <Typography variant="body2" sx={{ color: '#5f6368' }}>
              No inference requests logged yet this week.
            </Typography>
          ) : (
            Object.entries(currentUsage.tokens_by_model).map(([model, tokens]) => {
              const spend = currentUsage.spend_by_model[model] || 0;
              const pct = currentUsage.total_tokens > 0 ? (tokens / currentUsage.total_tokens) * 100 : 0;
              return (
                <Box key={model} sx={{ mb: 2, '&:last-child': { mb: 0 } }}>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {model}
                    </Typography>
                    <Typography variant="body2" sx={{ color: '#202124', fontWeight: 600 }}>
                      ${spend.toFixed(2)} ({tokens.toLocaleString()} tokens)
                    </Typography>
                  </Box>
                  <LinearProgress
                    variant="determinate"
                    value={pct}
                    sx={{ height: 6, borderRadius: 3, bgcolor: '#e8eaed' }}
                  />
                </Box>
              );
            })
          )}
        </Paper>

        {/* Hinge Point Breakdown */}
        <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
          Hinge Point Cost Distribution
        </Typography>
        <Paper elevation={0} sx={{ p: 2, mb: 3, border: '1px solid #dadce0', bgcolor: '#ffffff' }}>
          <Grid container spacing={1}>
            <Grid item xs={4}>
              <Typography variant="caption" sx={{ color: '#5f6368' }}>
                Input Tokens
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                ${breakdown.input_spend_usd.toFixed(2)}
              </Typography>
            </Grid>
            <Grid item xs={4}>
              <Typography variant="caption" sx={{ color: '#5f6368' }}>
                Output Tokens
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                ${breakdown.output_spend_usd.toFixed(2)}
              </Typography>
            </Grid>
            <Grid item xs={4}>
              <Typography variant="caption" sx={{ color: '#5f6368' }}>
                Cached Tokens
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                ${breakdown.cached_spend_usd.toFixed(2)}
              </Typography>
            </Grid>
          </Grid>
        </Paper>

        {/* Quota Management Controls */}
        <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
          Developer Governance & Quota Overrides
        </Typography>
        <Paper elevation={0} sx={{ p: 2.5, border: '1px solid #dadce0', bgcolor: '#ffffff' }}>
          {/* Custom Quota Toggle */}
          <FormControlLabel
            control={
              <Switch
                checked={hasCustomQuota}
                onChange={(e) => setHasCustomQuota(e.target.checked)}
                color="primary"
              />
            }
            label={
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  Enable Custom Quota Limit
                </Typography>
                <Typography variant="caption" sx={{ color: '#5f6368' }}>
                  Overrides global default (${config?.default_quota_usd.toFixed(2)})
                </Typography>
              </Box>
            }
            sx={{ mb: 2, display: 'flex' }}
          />

          {hasCustomQuota && (
            <Box sx={{ pl: 2, mb: 2, borderLeft: '3px solid #1a73e8' }}>
              <FormControl component="fieldset" sx={{ mb: 2, width: '100%' }}>
                <FormLabel component="legend" sx={{ fontSize: 13, fontWeight: 600 }}>
                  Weekly Credit Presets
                </FormLabel>
                <RadioGroup
                  row
                  value={selectedPreset}
                  onChange={(e) => handlePresetChange(e.target.value)}
                  sx={{ gap: 1, mt: 0.5 }}
                >
                  {(config?.preset_quotas || [10, 15, 25, 50, 100]).map((p) => (
                    <FormControlLabel
                      key={p}
                      value={String(p)}
                      control={<Radio size="small" />}
                      label={`$${p}`}
                    />
                  ))}
                  <FormControlLabel value="custom" control={<Radio size="small" />} label="Custom" />
                </RadioGroup>
              </FormControl>

              <Grid container spacing={2}>
                <Grid item xs={6}>
                  <TextField
                    label="Quota Limit ($)"
                    size="small"
                    fullWidth
                    value={customQuotaInput}
                    onChange={(e) => {
                      setSelectedPreset('custom');
                      setCustomQuotaInput(e.target.value);
                    }}
                    InputProps={{
                      startAdornment: <InputAdornment position="start">$</InputAdornment>,
                    }}
                  />
                </Grid>
                <Grid item xs={6}>
                  <TextField
                    label="Overage Buffer ($)"
                    size="small"
                    fullWidth
                    value={customOverageInput}
                    onChange={(e) => setCustomOverageInput(e.target.value)}
                    InputProps={{
                      startAdornment: <InputAdornment position="start">$</InputAdornment>,
                    }}
                  />
                </Grid>
              </Grid>
            </Box>
          )}

          <Divider sx={{ my: 2 }} />

          {/* Exemption Toggle */}
          <FormControlLabel
            control={
              <Switch
                checked={isExempt}
                onChange={(e) => setIsExempt(e.target.checked)}
                color="secondary"
              />
            }
            label={
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 600, color: isExempt ? '#7b1fa2' : '#202124' }}>
                  Exempt from Quota Throttling
                </Typography>
                <Typography variant="caption" sx={{ color: '#5f6368' }}>
                  Developer will never be throttled even if hard limit is breached.
                </Typography>
              </Box>
            }
            sx={{ mb: 2, display: 'flex' }}
          />

          {/* Manual Lock Toggle */}
          <FormControlLabel
            control={
              <Switch
                checked={isLocked}
                onChange={(e) => setIsLocked(e.target.checked)}
                color="error"
              />
            }
            label={
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 600, color: isLocked ? '#d93025' : '#202124' }}>
                  Manually Lock Access
                </Typography>
                <Typography variant="caption" sx={{ color: '#5f6368' }}>
                  Forces user into disabled group. Protected from automatic Monday resets.
                </Typography>
              </Box>
            }
            sx={{ display: 'flex' }}
          />
        </Paper>
      </Box>

      {/* Footer Actions */}
      <Box
        sx={{
          p: 2.5,
          backgroundColor: '#ffffff',
          borderTop: '1px solid #dadce0',
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 1.5,
        }}
      >
        <Button variant="outlined" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button
          variant="contained"
          startIcon={<SaveIcon />}
          onClick={handleSaveClick}
          disabled={saving}
        >
          {saving ? 'Saving...' : 'Save Draft'}
        </Button>
      </Box>
    </Drawer>
  );
};
