import { createTheme } from '@mui/material/styles';

export const theme = createTheme({
  palette: {
    primary: {
      main: '#1a73e8', // Google Blue
      light: '#e8f0fe',
      dark: '#1557b0',
      contrastText: '#ffffff',
    },
    secondary: {
      main: '#188038', // Google Green
      light: '#e6f4ea',
      dark: '#137333',
      contrastText: '#ffffff',
    },
    warning: {
      main: '#f9ab00', // Google Amber / Yellow
      light: '#fef7e0',
      dark: '#e37400',
    },
    error: {
      main: '#d93025', // Google Red
      light: '#fce8e6',
      dark: '#c5221f',
    },
    info: {
      main: '#1a73e8',
      light: '#e8f0fe',
      dark: '#1557b0',
    },
    background: {
      default: '#f8f9fa',
      paper: '#ffffff',
    },
    text: {
      primary: '#202124',
      secondary: '#5f6368',
    },
    divider: '#dadce0',
  },
  typography: {
    fontFamily: '"Google Sans", "Roboto", "Helvetica", "Arial", sans-serif',
    h5: {
      fontWeight: 600,
      letterSpacing: '-0.2px',
    },
    h6: {
      fontWeight: 600,
      letterSpacing: '-0.1px',
    },
    subtitle1: {
      fontWeight: 500,
    },
    subtitle2: {
      fontWeight: 500,
      color: '#5f6368',
    },
    button: {
      textTransform: 'none',
      fontWeight: 500,
      borderRadius: 8,
    },
  },
  shape: {
    borderRadius: 8,
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          boxShadow: 'none',
          '&:hover': {
            boxShadow: '0 1px 2px 0 rgba(60,64,67,0.3), 0 1px 3px 1px rgba(60,64,67,0.15)',
          },
        },
        containedPrimary: {
          backgroundColor: '#1a73e8',
          '&:hover': {
            backgroundColor: '#174ea6',
          },
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        rounded: {
          borderRadius: 12,
        },
        elevation1: {
          boxShadow: '0 1px 3px 0 rgba(60,64,67,0.3), 0 4px 8px 3px rgba(60,64,67,0.15)',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          fontWeight: 500,
          borderRadius: 6,
        },
      },
    },
  },
});
