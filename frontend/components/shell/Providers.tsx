'use client';

import { OperatorProvider } from '@/lib/operator';
import { WorldProvider } from '@/lib/world';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <OperatorProvider>
      <WorldProvider>{children}</WorldProvider>
    </OperatorProvider>
  );
}
