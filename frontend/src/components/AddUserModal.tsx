import React, { useState } from 'react';
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Grid,
  InputAdornment,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import { AppConfig } from '../types';

interface AddUserModalProps {
  config: AppConfig | null;
  open: boolean;
  onClose: () => void;
  onAdd: (payload: {
    email: string;
    is_exempt?: boolean;
    has_custom_quota?: boolean;
    custom_quota_usd?: number | null;
    custom_overage_usd?: number | null;
  }) => Promise<void>;
}

export const AddUserModal: React.FC<AddUserModalProps> = ({
  config,
  open,
  onClose,
  onAdd,
}) => {
  const [email, setEmail] = useState('');
  const [hasCustomQuota, setHasCustomQuota] = useState(false);
  const [customQuota, setCustomQuota] = useState('10.00');
  const [customOverage, setCustomOverage] = useState('2.00');
  const [isExempt, setIsExempt] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleAdd = async () => {
    if (!email || !email.includes('@')) {
      setError('Please enter a valid developer email address.');
      return;
    }
    setError('');
    setSaving(true);
    try {
      await onAdd({
        email: email.trim(),
        is_exempt: isExempt,
        has_custom_quota: hasCustomQuota,
        custom_quota_usd: hasCustomQuota ? parseFloat(customQuota) : null,
        custom_overage_usd: hasCustomQuota ? parseFloat(customOverage) : null,
      });
      setEmail('');
      setHasCustomQuota(false);
      setIsExempt(false);
      onClose();
    } catch (e: any) {
      setError(e.message || 'Failed to add developer.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <PersonAddIcon sx={{ color: '#1a73e8' }} />
        Pre-provision Developer
      </DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" sx={{ color: '#5f6368', mb: 2 }}>
          Grant initial credit allocation and add developer to <strong>{config?.enabled_group || 'antigravity-enabled@'}</strong>.
        </Typography>

        <TextField
          fullWidth
          size="small"
          label="Developer Email"
          placeholder="developer@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={Boolean(error)}
          helperText={error}
          sx={{ mb: 2 }}
        />

        <FormControlLabel
          control={
            <Switch
              checked={hasCustomQuota}
              onChange={(e) => setHasCustomQuota(e.target.checked)}
              color="primary"
            />
          }
          label={<Typography variant="body2">Custom Quota Limit</Typography>}
          sx={{ mb: 1.5, display: 'flex' }}
        />

        {hasCustomQuota && (
          <Grid container spacing={1.5} sx={{ mb: 2 }}>
            <Grid item xs={6}>
              <TextField
                fullWidth
                size="small"
                label="Weekly Quota"
                value={customQuota}
                onChange={(e) => setCustomQuota(e.target.value)}
                InputProps={{
                  startAdornment: <InputAdornment position="start">$</InputAdornment>,
                }}
              />
            </Grid>
            <Grid item xs={6}>
              <TextField
                fullWidth
                size="small"
                label="Overage Buffer"
                value={customOverage}
                onChange={(e) => setCustomOverage(e.target.value)}
                InputProps={{
                  startAdornment: <InputAdornment position="start">$</InputAdornment>,
                }}
              />
            </Grid>
          </Grid>
        )}

        <FormControlLabel
          control={
            <Switch
              checked={isExempt}
              onChange={(e) => setIsExempt(e.target.checked)}
              color="secondary"
            />
          }
          label={<Typography variant="body2">Exempt from Quota Throttling</Typography>}
          sx={{ display: 'flex' }}
        />
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button variant="contained" onClick={handleAdd} disabled={saving}>
          {saving ? 'Adding...' : 'Add Developer'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
