import type { Metadata, Viewport } from 'next';
import { Providers } from '@/components/shell/Providers';
import { Shell } from '@/components/shell/Shell';
import './fonts/fonts.css';
import './globals.css';

export const metadata: Metadata = {
  title: { default: 'FuelWatch dispatch board', template: '%s | FuelWatch' },
  description: 'Operator console for a national fuel-supply network: stock cover, shipment proposals, alerts.',
};
export const viewport: Viewport = { width: 'device-width', initialScale: 1, colorScheme: 'light dark' };

// Applies the saved theme and density before first paint so there is no flash. Wrapped in try/catch: storage can be blocked.
const bootstrap = `try{var d=document.documentElement,t=localStorage.getItem('fuelwatch.theme'),n=localStorage.getItem('fuelwatch.density');if(t==='light'||t==='dark')d.dataset.theme=t;if(n==='compact')d.dataset.density=n}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: bootstrap }} />
      </head>
      <body>
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
