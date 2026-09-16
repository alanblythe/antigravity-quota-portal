import React, { useEffect, useState } from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Grid,
  InputAdornment,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import PriceCheckIcon from '@mui/icons-material/PriceCheck';
import { ModelPricing } from '../types';

interface PricingModalProps {
  pricing: Record<string, ModelPricing>;
  open: boolean;
  onClose: () => void;
  onSave: (matrix: Record<string, ModelPricing>) => Promise<void>;
}

export const PricingModal: React.FC<PricingModalProps> = ({
  pricing,
  open,
  onClose,
  onSave,
}) => {
  const [matrix, setMatrix] = useState<Record<string, ModelPricing>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (pricing) {
      setMatrix(JSON.parse(JSON.stringify(pricing)));
    }
  }, [pricing, open]);

  const handleChange = (
    modelKey: string,
    field: keyof ModelPricing,
    value: string
  ) => {
    const num = parseFloat(value) || 0;
    setMatrix((prev) => ({
      ...prev,
      [modelKey]: {
        ...prev[modelKey],
        [field]: num,
      },
    }));
  };

  const calculateBlended = (p: ModelPricing) => {
    const rate =
      p.estimated_input_ratio * p.input_price_per_million +
      p.estimated_output_ratio * p.output_price_per_million +
      p.estimated_cached_ratio * p.cached_price_per_million;
    return rate.toFixed(4);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(matrix);
      onClose();
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <PriceCheckIcon sx={{ color: '#1a73e8' }} />
        Token Pricing & Hinge Point Matrix
      </DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" sx={{ color: '#5f6368', mb: 2 }}>
          Configure per-million token rates and estimated input/output/cached hinge ratios used to compute gross spend from Cloud Logging audit events.
        </Typography>

        <TableContainer component={Paper} elevation={0} sx={{ border: '1px solid #dadce0' }}>
          <Table size="small">
            <TableHead sx={{ backgroundColor: '#f1f3f4' }}>
              <TableRow>
                <TableCell sx={{ fontWeight: 600 }}>Model Name</TableCell>
                <TableCell align="center" sx={{ fontWeight: 600 }}>Input / 1M ($)</TableCell>
                <TableCell align="center" sx={{ fontWeight: 600 }}>Output / 1M ($)</TableCell>
                <TableCell align="center" sx={{ fontWeight: 600 }}>Cached / 1M ($)</TableCell>
                <TableCell align="center" sx={{ fontWeight: 600 }}>Hinge Ratios (In/Out/Cache)</TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>Blended / 1M</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {Object.entries(matrix).map(([key, model]) => (
                <TableRow key={key}>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {model.display_name}
                    </Typography>
                    <Typography variant="caption" sx={{ color: '#5f6368', fontFamily: 'monospace' }}>
                      Pattern: {model.log_pattern}
                    </Typography>
                  </TableCell>

                  <TableCell align="center">
                    <TextField
                      size="small"
                      type="number"
                      value={model.input_price_per_million}
                      onChange={(e) => handleChange(key, 'input_price_per_million', e.target.value)}
                      sx={{ width: 85 }}
                      inputProps={{ step: 0.01, min: 0 }}
                    />
                  </TableCell>

                  <TableCell align="center">
                    <TextField
                      size="small"
                      type="number"
                      value={model.output_price_per_million}
                      onChange={(e) => handleChange(key, 'output_price_per_million', e.target.value)}
                      sx={{ width: 85 }}
                      inputProps={{ step: 0.01, min: 0 }}
                    />
                  </TableCell>

                  <TableCell align="center">
                    <TextField
                      size="small"
                      type="number"
                      value={model.cached_price_per_million}
                      onChange={(e) => handleChange(key, 'cached_price_per_million', e.target.value)}
                      sx={{ width: 85 }}
                      inputProps={{ step: 0.005, min: 0 }}
                    />
                  </TableCell>

                  <TableCell align="center">
                    <Typography variant="caption" sx={{ fontWeight: 600 }}>
                      {(model.estimated_input_ratio * 100).toFixed(0)}% / {(model.estimated_output_ratio * 100).toFixed(0)}% / {(model.estimated_cached_ratio * 100).toFixed(0)}%
                    </Typography>
                  </TableCell>

                  <TableCell align="right">
                    <Typography variant="body2" sx={{ fontWeight: 700, color: '#1a73e8' }}>
                      ${calculateBlended(model)}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button variant="contained" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save Pricing Matrix'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
