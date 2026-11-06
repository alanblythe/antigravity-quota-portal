import React from 'react';
import { Box, Paper, Typography } from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';

export const DisclaimerBanner: React.FC = () => {
  return (
    <Paper
      elevation={0}
      sx={{
        p: 1.5,
        mb: 3,
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        backgroundColor: '#e8f0fe',
        border: '1px solid #d2e3fc',
        borderRadius: 2,
      }}
    >
      <InfoOutlinedIcon sx={{ color: '#1a73e8', fontSize: 22, flexShrink: 0 }} />
      <Typography variant="body2" sx={{ color: '#174ea6', fontWeight: 500, lineHeight: 1.4 }}>
        <strong>Weekly Reset Alignment:</strong> Each developer&apos;s spend counter and auto-throttle un-lock are <strong>phase-locked to their licensed project&apos;s 7-day GCP Weighted Token Units (WTU) sliding window reset</strong> (<Box component="code" sx={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>window_start_time</Box> + 7d). When a project&apos;s weekly tokens replenish, throttled developers in that project are automatically restored to <code>ACTIVE</code>.
      </Typography>
    </Paper>
  );
};
