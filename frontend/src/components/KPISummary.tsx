import React from 'react';
import { Box, Card, CardContent, Grid, Typography } from '@mui/material';
import PeopleAltOutlinedIcon from '@mui/icons-material/PeopleAltOutlined';
import BlockOutlinedIcon from '@mui/icons-material/BlockOutlined';
import TokenOutlinedIcon from '@mui/icons-material/TokenOutlined';
import AttachMoneyOutlinedIcon from '@mui/icons-material/AttachMoneyOutlined';
import AccountBalanceWalletOutlinedIcon from '@mui/icons-material/AccountBalanceWalletOutlined';
import WarningAmberOutlinedIcon from '@mui/icons-material/WarningAmberOutlined';
import { KPIStats } from '../types';

interface KPISummaryProps {
  kpis: KPIStats | null;
  loading?: boolean;
}

export const KPISummary: React.FC<KPISummaryProps> = ({ kpis, loading }) => {
  const formatTokens = (tokens: number) => {
    if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
    if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}k`;
    return tokens.toLocaleString();
  };

  const cards = [
    {
      title: 'Active Developers',
      value: kpis ? kpis.active_developers : '-',
      subtext: `${kpis?.exempt_developers || 0} Exempt`,
      icon: <PeopleAltOutlinedIcon sx={{ color: '#188038' }} />,
      bgColor: '#e6f4ea',
    },
    {
      title: 'Auto-Throttled',
      value: kpis ? kpis.auto_throttled_developers : '-',
      subtext: `${kpis?.manually_locked_developers || 0} Locked`,
      icon: <BlockOutlinedIcon sx={{ color: '#d93025' }} />,
      bgColor: '#fce8e6',
    },
    {
      title: 'Tokens This Week',
      value: kpis ? formatTokens(kpis.total_tokens_this_week) : '-',
      subtext: kpis?.week_id || 'Current Week',
      icon: <TokenOutlinedIcon sx={{ color: '#1a73e8' }} />,
      bgColor: '#e8f0fe',
    },
    {
      title: 'Total Gross Usage',
      value: kpis ? `$${kpis.total_gross_usage_usd.toFixed(2)}` : '-',
      subtext: 'Estimated Value',
      icon: <AttachMoneyOutlinedIcon sx={{ color: '#e37400' }} />,
      bgColor: '#fef7e0',
    },
    {
      title: 'Credits Allocated',
      value: kpis ? `$${kpis.total_credits_allocated_usd.toFixed(2)}` : '-',
      subtext: 'Granted Allowance',
      icon: <AccountBalanceWalletOutlinedIcon sx={{ color: '#1a73e8' }} />,
      bgColor: '#e8f0fe',
    },
    {
      title: 'Net Overages',
      value: kpis ? `$${kpis.total_net_overages_usd.toFixed(2)}` : '-',
      subtext: 'Above Allocated Quota',
      icon: <WarningAmberOutlinedIcon sx={{ color: '#d93025' }} />,
      bgColor: '#fce8e6',
    },
  ];

  return (
    <Grid container spacing={2} sx={{ mb: 3 }}>
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
  );
};
