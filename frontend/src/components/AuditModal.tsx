import React, { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from '@mui/material';
import HistoryIcon from '@mui/icons-material/History';
import { AuditEvent } from '../types';
import { fetchAuditEvents } from '../services/api';

interface AuditModalProps {
  open: boolean;
  onClose: () => void;
}

export const AuditModal: React.FC<AuditModalProps> = ({ open, onClose }) => {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setError('');
    fetchAuditEvents(100)
      .then((data) => {
        if (active) {
          setEvents(data);
        }
      })
      .catch((e: any) => {
        console.error(e);
        if (active) {
          setError(e.message || 'Failed to load audit events.');
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [open]);

  const loadEvents = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchAuditEvents(100);
      setEvents(data);
    } catch (e: any) {
      console.error(e);
      setError(e.message || 'Failed to load audit events.');
    } finally {
      setLoading(false);
    }
  };

  const formatUtcTimestamp = (isoStr: string) => {
    try {
      return (
        new Date(isoStr).toLocaleString('en-US', {
          timeZone: 'UTC',
          year: 'numeric',
          month: 'short',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        }) + ' UTC'
      );
    } catch {
      return isoStr;
    }
  };

  const getActionColor = (action: string) => {
    if (action.includes('SWAP') || action.includes('LOCK')) return '#d93025';
    if (action.includes('PUBLISH')) return '#188038';
    if (action.includes('DISCOVER')) return '#1a73e8';
    return '#5f6368';
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <HistoryIcon sx={{ color: '#1a73e8' }} />
          Audit & Governance Event Trail
        </Box>
        <Button size="small" onClick={loadEvents} disabled={loading}>
          Refresh
        </Button>
      </DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <TableContainer component={Paper} elevation={0} sx={{ border: '1px solid #dadce0', maxHeight: 500 }}>
          <Table stickyHeader size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontWeight: 600, width: 195 }}>Timestamp (UTC)</TableCell>
                <TableCell sx={{ fontWeight: 600, width: 160 }}>Action</TableCell>
                <TableCell sx={{ fontWeight: 600, width: 220 }}>Target Developer</TableCell>
                <TableCell sx={{ fontWeight: 600, width: 140 }}>Triggered By</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Details</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {events.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} align="center" sx={{ py: 3, color: '#5f6368' }}>
                    {loading ? 'Loading audit trail...' : error ? 'Unable to load audit events.' : 'No audit events recorded yet.'}
                  </TableCell>
                </TableRow>
              ) : (
                events.map((evt) => (
                  <TableRow key={evt.event_id} hover>
                    <TableCell sx={{ fontFamily: 'monospace', fontSize: 12, whiteSpace: 'nowrap' }}>
                      {formatUtcTimestamp(evt.timestamp)}
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={evt.action}
                        size="small"
                        sx={{
                          fontWeight: 600,
                          fontSize: 11,
                          color: getActionColor(evt.action),
                          bgcolor: `${getActionColor(evt.action)}15`,
                        }}
                      />
                    </TableCell>
                    <TableCell sx={{ fontWeight: 500 }}>{evt.target_user || '-'}</TableCell>
                    <TableCell sx={{ color: '#5f6368' }}>{evt.triggered_by}</TableCell>
                    <TableCell
                      sx={{
                        fontFamily: 'monospace',
                        fontSize: 12,
                        color: '#3c4043',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                      }}
                    >
                      {JSON.stringify(evt.details)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};
