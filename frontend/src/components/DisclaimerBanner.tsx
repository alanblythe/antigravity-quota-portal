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
        <strong>Estimation Notice:</strong> All token breakdown percentages, gross spend figures, credit balances, and net cost metrics displayed in this portal are estimated projections calculated from token usage logs and model pricing configurations.
      </Typography>
    </Paper>
  );
};
